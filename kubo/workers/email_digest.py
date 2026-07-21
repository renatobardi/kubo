"""Worker `email-digest` — envia o digest diário de UM destino de e-mail.

ADR-0031: credenciais SMTP vêm do construtor (montadas pela factory do sweep a
partir de env); o endereço (PII) também chega pelo construtor. O sender é
injetável para teste.
"""

from __future__ import annotations

from collections.abc import Callable

from kubo.contracts.models import WorkerManifest
from kubo.contracts.worker import DigestView, RunContext
from kubo.distribution.digest_email import build_email_digest
from kubo.distribution.email import SmtpConfig, send_email
from kubo.errors import SenderError
from kubo.store.destinations import Destination
from kubo.workers._digest_common import DigestConfig, _DigestWorker

EmailSender = Callable[..., None]

# Alias local para o manifest e isinstance do worker de e-mail; o schema é
# intencionalmente o mesmo do digest Telegram (só `max_items`).
EmailDigestConfig = DigestConfig


class EmailDigestWorker(_DigestWorker):
    """Envia o digest dos destilados novos para UM destino de e-mail.

    `destination`, `base_url` e `smtp_config` são injetados na construção.
    `email_sender` é injetável para teste; padrão usa `send_email`.
    """

    _channel = "email"
    _error_kind = "email_send"
    _config_class = EmailDigestConfig

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
        super().__init__(destination=destination, base_url=base_url)
        self._smtp_config = smtp_config
        self._email_sender: EmailSender = email_sender or send_email

    def _deliver(self, ctx: RunContext, views: list[DigestView]) -> None:
        """Monta o e-mail e envia; levanta SenderError se não puder entregar."""
        del ctx  # não precisamos de integrações para e-mail
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
