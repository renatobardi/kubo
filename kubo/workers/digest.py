"""Worker `digest` — envia o digest diário dos destilados novos (ADR-0015 §IV/§V).

Mecânico (sem LLM no circuito): para cada destino, lê os destilados novos via seam
(`distilled_for_digest`, que resolve o watermark por-destino + bootstrap), monta a
mensagem do canal e envia. Dia sem novidade num destino = nenhum dispatch (§V:
só-se-novidade). Falha de envio vira `dispatch(error)` estruturado + ErrorInfo —
o run não explode (ADR-0009 §VII), e o watermark do destino não avança (o perdido
reentra amanhã, retry-de-graça). Entrega at-least-once: crash entre o envio e o
persist re-envia amanhã (pior caso: digest duplicado pro dono — aborrecimento, não
corrupção; §IV). O runner NÃO muda: DispatchPayload é só mais um membro da união.

Despacho por canal é `if channel == ...` EXPLÍCITO (advisor): registry/plugin de
senders seria DSL disfarçada — a mesma postura do `_persist` do runner.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from kubo.contracts.models import DispatchPayload, ErrorInfo, RunResult, Stats, WorkerManifest
from kubo.contracts.worker import DigestView, RunContext
from kubo.distribution.destinations import ResolvedDestination
from kubo.distribution.digest import build_telegram_digest
from kubo.distribution.telegram import send_telegram
from kubo.errors import ContractError, SenderError

# Sender de um canal: recebe segredo/endereço/texto e ENVIA (ou levanta SenderError).
# Assinatura por-keyword para casar com `send_telegram`; injetável para teste.
Sender = Callable[..., None]
_MSG_CAP = 500  # teto da mensagem de erro (ADR-0009 §VIII) — sem vazar conteúdo/segredo


class DigestConfig(BaseModel):
    """Config declarada do worker `digest` (ADR-0015). `max_items` = teto de destilados
    por digest por destino; o excedente flui para o dia seguinte (watermark)."""

    model_config = ConfigDict(extra="forbid")

    max_items: int = 50


class DigestWorker:
    """Envia o digest dos destilados novos para cada destino declarado.

    `destinations`/`base_url` são injetados na construção (o scheduler os resolve do
    `destinations.yaml` + env — o worker não lê `os.environ`). `senders` mapeia canal
    → função de envio; default usa o sender real do Telegram, teste injeta um fake."""

    manifest = WorkerManifest(
        name="digest", version="1", integrations=["telegram"], config=DigestConfig
    )

    def __init__(
        self,
        *,
        destinations: list[ResolvedDestination],
        base_url: str,
        senders: Mapping[str, Sender] | None = None,
    ) -> None:
        """Guarda destinos resolvidos, base URL do link e o mapa de senders por canal."""
        self._destinations = destinations
        self._base_url = base_url
        self._senders: Mapping[str, Sender] = senders or {"telegram": send_telegram}

    def run(self, ctx: RunContext) -> RunResult:
        """Para cada destino com novidade, monta e envia o digest e devolve um
        DispatchPayload; sem novidade não gera dispatch (§V). Falha de envio vira
        dispatch(error) + ErrorInfo(dispatch_partial) — o run fecha com o parcial."""
        config = ctx.config
        if not isinstance(config, DigestConfig):  # narrowing (padrão do FeedWorker)
            raise ContractError(
                f"DigestWorker recebeu config {type(config).__name__}, esperava DigestConfig"
            )

        payloads: list[DispatchPayload] = []
        new_total = 0
        failed = 0

        for dest in self._destinations:
            views = ctx.knowledge.distilled_for_digest(dest.id, config.max_items)
            if not views:
                continue  # só-se-novidade: nenhum dispatch, coerente com ADR-0010
            new_total += len(views)
            watermark = max(v.created_at for v in views)
            items = [v.id for v in views]
            try:
                self._deliver(ctx, dest, views)
                payloads.append(_ok_dispatch(dest, watermark, items))
            except SenderError as exc:
                failed += 1
                ctx.logger.warning("digest.send_failed", destination=dest.id, channel=dest.channel)
                payloads.append(_error_dispatch(dest, watermark, items, exc))

        error = (
            ErrorInfo(kind="dispatch_partial", message=f"{failed} destino(s) falharam no envio")
            if failed
            else None
        )
        stats = Stats.model_validate(
            {"new_distilled": new_total, "dispatched": len(payloads) - failed, "failed": failed}
        )
        return RunResult(payloads=list(payloads), stats=stats, error=error)

    def _deliver(self, ctx: RunContext, dest: ResolvedDestination, views: list[DigestView]) -> None:
        """Monta a mensagem do canal e envia; levanta SenderError se não puder entregar.
        Despacho por canal EXPLÍCITO — e-mail entra no 12.9 (fora desta fatia)."""
        sender = self._senders.get(dest.channel)
        if sender is None:
            raise SenderError(f"canal {dest.channel!r} sem sender configurado")
        if dest.channel == "telegram":
            token = _integration_secret(ctx, "telegram")
            text = build_telegram_digest(views, self._base_url)
            sender(token=token, chat_id=dest.address, text=text)
        else:  # email = 12.9; qualquer outro canal declarado sem suporte falha limpo
            raise SenderError(f"canal {dest.channel!r} não suportado nesta sessão")


def _integration_secret(ctx: RunContext, name: str) -> str:
    """Lê o segredo resolvido de uma integração do ctx; ausente → SenderError (o worker
    nunca lê env, e um token faltando é falha de ENTREGA, não crash do run)."""
    integration = ctx.integrations.get(name)
    secret = getattr(integration, "secret", None)
    if not secret:
        raise SenderError(f"integração {name!r} sem segredo resolvido no ctx")
    return str(secret)


def _ok_dispatch(dest: ResolvedDestination, watermark: Any, items: list[str]) -> DispatchPayload:
    """DispatchPayload de entrega bem-sucedida."""
    return DispatchPayload(
        destination=dest.id,
        channel=dest.channel,
        status="ok",
        artifact="digest",
        watermark=watermark,
        item_count=len(items),
        items=items,
    )


def _error_dispatch(
    dest: ResolvedDestination, watermark: Any, items: list[str], exc: SenderError
) -> DispatchPayload:
    """DispatchPayload de falha: watermark da TENTATIVA (não avança seleção — só `ok`
    avança) + erro estruturado (mensagem já redigida pelo sender, sem token)."""
    return DispatchPayload(
        destination=dest.id,
        channel=dest.channel,
        status="error",
        artifact="digest",
        watermark=watermark,
        item_count=len(items),
        items=items,
        error=ErrorInfo(kind=f"{dest.channel}_send", message=str(exc)[:_MSG_CAP]),
    )
