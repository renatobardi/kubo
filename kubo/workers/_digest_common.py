"""Base compartilhada pelos workers de digest de um destino (ADR-0029/0031).

Mantém o ciclo comum: ler destilados novos, entregar, persistir payload ok/error.
Cada canal herda `_DigestWorker` e implementa apenas `_deliver`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from kubo.contracts.models import DispatchPayload, ErrorInfo, RunResult, Stats
from kubo.contracts.worker import DigestView, RunContext
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
        """Para o destino configurado, monta e envia o digest e devolve um
        DispatchPayload ok ou error. Sem novidade devolve RunResult vazio."""
        config = ctx.config
        expected = self._config_class.__name__
        if not isinstance(config, self._config_class):
            raise ContractError(
                f"{type(self).__name__} recebeu config {type(config).__name__}, esperava {expected}"
            )

        destination_id = str(self._destination.id)
        views = ctx.knowledge.distilled_for_digest(
            destination_id,
            config.max_items,  # type: ignore[attr-defined]
        )
        if not views:
            return _empty_run()

        watermark = max(v.created_at for v in views)
        items = [v.id for v in views]

        try:
            self._deliver(ctx, views)
            payload = _payload(self._destination, self._channel, watermark, items, status="ok")
            return _run_result(payload, failed=False, new_distilled=len(views))
        except SenderError as exc:
            payload = _payload(
                self._destination,
                self._channel,
                watermark,
                items,
                status="error",
                error=ErrorInfo(kind=self._error_kind, message=str(exc)[:_MSG_CAP]),
            )
            return _run_result(payload, failed=True, new_distilled=len(views))

    def _deliver(self, ctx: RunContext, views: list[DigestView]) -> None:
        """Manda o digest pelo canal; levanta SenderError se não puder entregar."""
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
    """DispatchPayload de entrega (ok ou error) com watermark da tentativa."""
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


def _empty_run() -> RunResult:
    """RunResult para quando não há novidade (só-se-novidade, ADR-0015 §V)."""
    return RunResult(
        payloads=[],
        stats=Stats.model_validate({"new_distilled": 0, "dispatched": 0, "failed": 0}),
    )
