"""Worker `email-digest` — envia o digest diário de UM destino de e-mail.

ADR-0031: credenciais SMTP vêm do construtor (montadas pela factory do sweep a
partir de env); o endereço (PII) também chega pelo construtor. O sender é
injetável para teste.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from kubo.contracts.models import DispatchPayload, ErrorInfo, RunResult, Stats, WorkerManifest
from kubo.contracts.worker import DigestView, RunContext
from kubo.distribution.digest_email import build_email_digest
from kubo.distribution.email import SmtpConfig, send_email
from kubo.errors import ContractError, SenderError
from kubo.store.destinations import Destination

EmailSender = Callable[..., None]
_MSG_CAP = 500


class EmailDigestConfig(BaseModel):
    """Config declarada do worker de e-mail: só `max_items`.

    Espelha `DigestConfig` do Telegram; definida localmente para evitar import
    cruzado, mas semanticamente idêntica.
    """

    model_config = ConfigDict(extra="forbid")

    max_items: int = 50


class EmailDigestWorker:
    """Envia o digest dos destilados novos para UM destino de e-mail.

    `destination`, `base_url` e `smtp_config` são injetados na construção.
    `email_sender` é injetável para teste; padrão usa `send_email`.
    """

    manifest = WorkerManifest(
        name="email-digest",
        version="1",
        integrations=[],
        config=EmailDigestConfig,
    )

    def __init__(
        self,
        *,
        destination: Destination,
        base_url: str,
        smtp_config: SmtpConfig | None,
        email_sender: EmailSender | None = None,
    ) -> None:
        self._destination = destination
        self._base_url = base_url
        self._smtp_config = smtp_config
        self._email_sender: EmailSender = email_sender or send_email

    def __repr__(self) -> str:
        return f"EmailDigestWorker(destination={self._destination!r}, base_url={self._base_url!r})"

    def run(self, ctx: RunContext) -> RunResult:
        """Para o destino configurado, monta e envia o digest e devolve um
        DispatchPayload ok ou error. Sem novidade devolve RunResult vazio."""
        config = ctx.config
        if not isinstance(config, EmailDigestConfig):
            raise ContractError(
                f"EmailDigestWorker recebeu config {type(config).__name__}, "
                f"esperava EmailDigestConfig"
            )

        destination_id = str(self._destination.id)
        views = ctx.knowledge.distilled_for_digest(destination_id, config.max_items)
        if not views:
            return _empty_run()

        watermark = max(v.created_at for v in views)
        items = [v.id for v in views]

        try:
            self._deliver(views)
            payload = _payload(self._destination, watermark, items, status="ok")
            return _run_result(payload, failed=False, new_distilled=len(views))
        except SenderError as exc:
            payload = _payload(
                self._destination,
                watermark,
                items,
                status="error",
                error=ErrorInfo(kind="email_send", message=str(exc)[:_MSG_CAP]),
            )
            return _run_result(payload, failed=True, new_distilled=len(views))

    def _deliver(self, views: list[DigestView]) -> None:
        """Monta o e-mail e envia; levanta SenderError se não puder entregar."""
        if self._destination.channel != "email":
            raise SenderError(
                f"canal {self._destination.channel!r} não suportado por EmailDigestWorker"
            )
        if self._smtp_config is None:
            raise SenderError("configuração SMTP não fornecida")
        built = build_email_digest(views, self._base_url)
        if built is None:
            return
        subject, text_body, html_body = built
        self._email_sender(
            to=self._destination.address,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            smtp_config=self._smtp_config,
        )


def _payload(
    destination: Destination,
    watermark: Any,
    items: list[str],
    *,
    status: Literal["ok", "error"],
    error: ErrorInfo | None = None,
) -> DispatchPayload:
    """DispatchPayload de entrega (ok ou error) com watermark da tentativa."""
    return DispatchPayload(
        destination=str(destination.id),
        channel="email",
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
    """RunResult para quando não há novidade."""
    return RunResult(
        payloads=[],
        stats=Stats.model_validate({"new_distilled": 0, "dispatched": 0, "failed": 0}),
    )
