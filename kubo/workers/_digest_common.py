"""Base compartilhada pelos workers de digest de um destino (ADR-0029/0031, ADR-0050).

Mantém o ciclo comum: selecionar itens por janela de publicação, entregar, persistir
payload ok/error. As quatro formas de mensagem (normal, empty_window, none_passed,
recovery) são tratadas aqui — o worker nunca fica em silêncio (ADR-0050 revoga o
só-se-novidade). Cada canal herda `_DigestWorker` e implementa apenas `_deliver`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from kubo.contracts.models import DispatchPayload, ErrorInfo, RunResult, Stats
from kubo.contracts.worker import DigestSelectionView, RunContext
from kubo.errors import ContractError, SenderError
from kubo.store.destinations import Destination

_MSG_CAP = 500  # teto da mensagem de erro (ADR-0009 §VIII) — sem vazar conteúdo/segredo


class DigestConfig(BaseModel):
    """Config padrão de um worker de digest: só `max_items` (constante pinada
    pelo scheduler). O endereço do destino NÃO entra aqui (ADR-0029 §3)."""

    model_config = ConfigDict(extra="forbid")

    max_items: int = 50


class _DigestWorker:
    """Worker genérico de digest de UM destino.

    Subclasses definem `_channel`, `_error_kind`, `_config_class` e `manifest`,
    e implementam `_deliver` para o meio específico (Telegram, e-mail, etc.).
    """

    _channel: Literal["telegram", "email"]
    _error_kind: str
    _config_class: type[BaseModel]

    def __init__(self, *, destination: Destination, base_url: str) -> None:
        self._destination = destination
        self._base_url = base_url

    def run(self, ctx: RunContext) -> RunResult:
        """Para o destino configurado, seleciona itens por janela de publicação,
        monta e envia o digest (ou aviso), e devolve um DispatchPayload ok ou error.

        As quatro formas de mensagem (ADR-0050 §VI) são tratadas aqui:
        - normal/recovery: envia o digest com os itens selecionados.
        - empty_window/none_passed: envia um aviso curto (item_count=0).
        Todas produzem DispatchPayload com status=ok e watermark=window_end.
        O só-se-novidade (ADR-0015 §V) é revogado: Kubo nunca fica em silêncio."""
        config = ctx.config
        expected = self._config_class.__name__
        if not isinstance(config, self._config_class):
            raise ContractError(
                f"{type(self).__name__} recebeu config {type(config).__name__}, esperava {expected}"
            )

        destination_id = str(self._destination.id)
        selection = ctx.knowledge.items_for_digest(
            destination_id,
            config.max_items,  # type: ignore[attr-defined]
        )
        items = [v.id for v in selection.items]
        watermark = selection.watermark

        try:
            self._deliver(ctx, selection)
            payload = _payload(self._destination, self._channel, watermark, items, status="ok")
            return _run_result(payload, failed=False, new_distilled=len(selection.items))
        except SenderError as exc:
            payload = _payload(
                self._destination,
                self._channel,
                watermark,
                items,
                status="error",
                error=ErrorInfo(kind=self._error_kind, message=str(exc)[:_MSG_CAP]),
            )
            return _run_result(payload, failed=True, new_distilled=len(selection.items))

    def _deliver(self, ctx: RunContext, selection: DigestSelectionView) -> None:
        """Manda o digest (ou aviso) pelo canal; levanta SenderError se não puder
        entregar. A forma da mensagem é determinada por `selection.form`."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(destination={self._destination!r}, base_url={self._base_url!r})"
        )


def _payload(
    destination: Destination,
    channel: Literal["telegram", "email"],
    watermark: Any,
    items: list[str],
    *,
    status: Literal["ok", "error"],
    error: ErrorInfo | None = None,
) -> DispatchPayload:
    """DispatchPayload de entrega (ok ou error) com watermark da janela (ADR-0050 §IV)."""
    return DispatchPayload(
        destination=str(destination.id),
        channel=channel,
        status=status,
        artifact="digest",
        watermark=watermark,
        item_count=len(items),
        items=items,
        error=error,
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
