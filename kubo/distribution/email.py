"""Sender SMTP (ADR-0031) — envia e-mail multipart text/html.

Credenciais vêm de `SmtpConfig` (env montado pela factory do scheduler). Erros do
`smtplib` são sanitizados: a senha é redigida antes de virar `SenderError`.
STARTTLS é obrigatório em portas não-465; 465 usa SMTP_SSL.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Callable

from kubo.errors import SenderError

_REDACTED = "<password-redacted>"
_Sender = Callable[[str, str, EmailMessage], None] | None
_TIMEOUT = 15.0


@dataclass(frozen=True)
class SmtpConfig:
    """Configuração SMTP. `password` é segredo — `repr=False`."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    from_address: str


def email_smtp_config() -> SmtpConfig | None:
    """Monta `SmtpConfig` a partir do ambiente; retorna None se dados estiverem incompletos.

    Falta silenciosa aqui: a rota/worker levanta `SenderError` no uso (ADR-0031).
    """
    host = os.environ.get("KUBO_EMAIL_HOST", "").strip()
    port_raw = os.environ.get("KUBO_EMAIL_PORT", "").strip()
    user = os.environ.get("KUBO_EMAIL_USER", "").strip()
    password = os.environ.get("KUBO_EMAIL_PASSWORD", "").strip()
    from_address = os.environ.get("KUBO_EMAIL_FROM", "").strip()
    if not all((host, port_raw, user, password, from_address)):
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    return SmtpConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        from_address=from_address,
    )


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
    smtp_config: SmtpConfig | None,
    sender: _Sender = None,
) -> None:
    """Envia e-mail multipart para `to`. Levanta `SenderError` em qualquer falha.

    `sender` é injetável para teste; em produção usa o cliente `smtplib` real."""
    if smtp_config is None:
        raise SenderError("configuração SMTP não fornecida")
    if not smtp_config.host:
        raise SenderError("host SMTP não configurado")
    if not smtp_config.user:
        raise SenderError("usuário SMTP não configurado")
    if not smtp_config.password:
        raise SenderError("senha SMTP não configurada")

    msg = EmailMessage()
    msg["From"] = smtp_config.from_address or smtp_config.user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if sender is not None:
        sender(to, msg["From"], msg)
        return

    _send_with_smtplib(msg, smtp_config)


def _send_with_smtplib(msg: EmailMessage, config: SmtpConfig) -> None:
    """Envia via smtplib real: SSL para 465, STARTTLS obrigatório para as demais."""
    password = config.password
    try:
        if config.port == 465:
            with smtplib.SMTP_SSL(config.host, config.port, timeout=_TIMEOUT) as server:
                server.ehlo()
                server.login(config.user, password)
                server.send_message(msg)
            return

        with smtplib.SMTP(config.host, config.port, timeout=_TIMEOUT) as server:
            server.ehlo()
            if not server.has_extn("starttls"):
                raise SenderError("servidor SMTP não suporta STARTTLS")
            server.starttls()
            server.ehlo()
            server.login(config.user, password)
            server.send_message(msg)
    except SenderError:
        raise
    except Exception as exc:  # noqa: BLE001 — fronteira: exceção do smtplib vira SenderError
        raise SenderError(_sanitize(str(exc), password)) from None


def _sanitize(text: str, password: str) -> str:
    """Remove a senha do texto da exceção — 2ª cerca contra vazamento."""
    return text.replace(password, _REDACTED) if password else text
