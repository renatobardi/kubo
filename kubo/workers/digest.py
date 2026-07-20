"""Worker `telegram-digest` — envia o digest diário de UM destino Telegram.

ADR-0029: o digest vira sweep de destinos; cada canal ganha seu próprio worker,
single-destino. O endereço (PII) chega pelo CONSTRUTOR, nunca pela config ou
pelo payload; o sender é injetável para teste.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from kubo.contracts.models import DispatchPayload, ErrorInfo, RunResult, Stats, WorkerManifest
from kubo.contracts.worker import DigestView, RunContext
from kubo.distribution.digest import build_telegram_digest
from kubo.distribution.telegram import send_telegram
from kubo.errors import ContractError, SenderError
from kubo.store.destinations import Destination

# Sender de um canal: recebe segredo/endereço/texto e ENVIA (ou levanta SenderError).
# Assinatura por-keyword para casar com `send_telegram`; injetável para teste.
Sender = Callable[..., None]
_MSG_CAP = 500  # teto da mensagem de erro (ADR-0009 §VIII) — sem vazar conteúdo/segredo


class DigestConfig(BaseModel):
    """Config declarada do worker de digest: só `max_items` (constante pinada
    pelo scheduler). O endereço do destino NÃO entra aqui (ADR-0029 §3)."""

    model_config = ConfigDict(extra="forbid")

    max_items: int = 50


class TelegramDigestWorker:
    """Envia o digest dos destilados novos para UM destino Telegram.

    `destination` e `base_url` são injetados na construção (o scheduler resolve
    do banco + env). `sender` é injetável para teste; padrão usa o sender real.
    """

    manifest = WorkerManifest(
        name="telegram-digest",
        version="1",
        integrations=["telegram"],
        config=DigestConfig,
    )

    def __init__(
        self,
        *,
        destination: Destination,
        base_url: str,
        sender: Sender | None = None,
    ) -> None:
        """Guarda destino (address é PII, repr=False), base URL do link e sender."""
        self._destination = destination
        self._base_url = base_url
        self._sender: Sender = sender or send_telegram

    def run(self, ctx: RunContext) -> RunResult:
        """Para o destino configurado, monta e envia o digest e devolve um
        DispatchPayload ok ou error. Sem novidade devolve RunResult vazio."""
        config = ctx.config
        if not isinstance(config, DigestConfig):  # narrowing (padrão do FeedWorker)
            raise ContractError(
                f"TelegramDigestWorker recebeu config {type(config).__name__}, "
                f"esperava DigestConfig"
            )

        destination_id = str(self._destination.id)
        views = ctx.knowledge.distilled_for_digest(destination_id, config.max_items)
        if not views:
            return _empty_run()

        watermark = max(v.created_at for v in views)
        items = [v.id for v in views]

        try:
            self._deliver(ctx, views)
            payload = _ok_payload(self._destination, watermark, items)
            return _run_result(payload, failed=False, new_distilled=len(views))
        except SenderError as exc:
            payload = _error_payload(self._destination, watermark, items, exc)
            return _run_result(payload, failed=True, new_distilled=len(views))

    def _deliver(self, ctx: RunContext, views: list[DigestView]) -> None:
        """Monta a mensagem do Telegram e envia; levanta SenderError se não puder
        entregar."""
        if self._destination.channel != "telegram":
            raise SenderError(
                f"canal {self._destination.channel!r} não suportado por TelegramDigestWorker"
            )
        token = _integration_secret(ctx, "telegram")
        text = build_telegram_digest(views, self._base_url)
        self._sender(token=token, chat_id=self._destination.address, text=text)


def _integration_secret(ctx: RunContext, name: str) -> str:
    """Lê o segredo resolvido de uma integração do ctx; ausente → SenderError."""
    integration = ctx.integrations.get(name)
    secret = getattr(integration, "secret", None)
    if not secret:
        raise SenderError(f"integração {name!r} sem segredo resolvido no ctx")
    return str(secret)


def _ok_payload(destination: Destination, watermark: Any, items: list[str]) -> DispatchPayload:
    """DispatchPayload de entrega bem-sucedida."""
    return DispatchPayload(
        destination=str(destination.id),
        channel="telegram",
        status="ok",
        artifact="digest",
        watermark=watermark,
        item_count=len(items),
        items=items,
    )


def _error_payload(
    destination: Destination, watermark: Any, items: list[str], exc: SenderError
) -> DispatchPayload:
    """DispatchPayload de falha: watermark da TENTATIVA (não avança seleção — só
    `ok` avança) + erro estruturado (mensagem já redigida pelo sender, sem token)."""
    return DispatchPayload(
        destination=str(destination.id),
        channel="telegram",
        status="error",
        artifact="digest",
        watermark=watermark,
        item_count=len(items),
        items=items,
        error=ErrorInfo(kind="telegram_send", message=str(exc)[:_MSG_CAP]),
    )


def _run_result(payload: DispatchPayload, *, failed: bool, new_distilled: int) -> RunResult:
    """Envelope de RunResult com o payload único e stats."""
    stats = Stats.model_validate(
        {
            "new_distilled": new_distilled,
            "dispatched": 0 if failed else 1,
            "failed": 1 if failed else 0,
        }
    )
    return RunResult(payloads=[payload], stats=stats, error=payload.error)


def _empty_run() -> RunResult:
    """RunResult para quando não há novidade (só-se-novidade, ADR-0015 §V)."""
    return RunResult(
        payloads=[],
        stats=Stats.model_validate({"new_distilled": 0, "dispatched": 0, "failed": 0}),
    )
