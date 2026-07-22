"""Sender SMTP (ADR-0031) — unit puro, sem rede real.

Credenciais nunca vazam para erro/log; senha vive em `SmtpConfig.password` com
`repr=False`. STARTTLS obrigatório em portas não-465; 465 usa SMTP_SSL.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kubo.distribution.email import SmtpConfig, email_smtp_config, send_email
from kubo.errors import SenderError

_TEST_PASSWORD = "app-password"  # pragma: allowlist secret


def _config(**kwargs: object) -> SmtpConfig:
    defaults: dict[str, object] = {
        "host": "smtp.example.com",
        "port": 587,
        "user": "kubo@example.com",
        "password": _TEST_PASSWORD,
        "from_address": "kubo@example.com",
    }
    defaults.update(kwargs)
    return SmtpConfig(**defaults)  # type: ignore[arg-type]


def test_smtp_config_hides_password_in_repr() -> None:
    """A senha não aparece no repr do SmtpConfig."""
    cfg = _config()
    assert _TEST_PASSWORD not in repr(cfg)
    assert cfg.password == _TEST_PASSWORD


def test_send_with_starttls_success() -> None:
    """Porta 587: cria SMTP, starttls, login, sendmail."""
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)
    mock_smtp.has_extn.return_value = True

    with patch("kubo.distribution.email.smtplib.SMTP", return_value=mock_smtp):
        send_email(
            to="owner@example.com",
            subject="Kubo digest",
            text_body="plain",
            html_body="<p>html</p>",
            smtp_config=_config(port=587),
        )

    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("kubo@example.com", _TEST_PASSWORD)
    mock_smtp.send_message.assert_called_once()
    msg = mock_smtp.send_message.call_args.args[0]
    assert msg["From"] == "kubo@example.com"
    assert msg["To"] == "owner@example.com"
    assert msg["Subject"] == "Kubo digest"


def test_send_with_ssl_success() -> None:
    """Porta 465: cria SMTP_SSL, login, sendmail (sem starttls)."""
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    with patch("kubo.distribution.email.smtplib.SMTP_SSL", return_value=mock_smtp):
        send_email(
            to="owner@example.com",
            subject="Kubo digest",
            text_body="plain",
            html_body="<p>html</p>",
            smtp_config=_config(port=465),
        )

    mock_smtp.starttls.assert_not_called()
    mock_smtp.login.assert_called_once_with("kubo@example.com", _TEST_PASSWORD)
    mock_smtp.send_message.assert_called_once()


def test_starttls_required_for_non_ssl_port() -> None:
    """Porta não-465 sem STARTTLS disponível → SenderError, sem envio."""
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)
    mock_smtp.has_extn.return_value = False
    cfg = _config(port=587)

    with (
        patch("kubo.distribution.email.smtplib.SMTP", return_value=mock_smtp),
        pytest.raises(SenderError),
    ):
        send_email(
            to="owner@example.com",
            subject="Kubo digest",
            text_body="plain",
            html_body="<p>html</p>",
            smtp_config=cfg,
        )

    mock_smtp.send_message.assert_not_called()


def test_auth_error_does_not_leak_password() -> None:
    """Exceção do smtplib é sanitizada: a senha não aparece na mensagem."""
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)
    mock_smtp.has_extn.return_value = True
    mock_smtp.login.side_effect = Exception(f"Authentication failed: {_TEST_PASSWORD} rejected")
    cfg = _config()

    with patch("kubo.distribution.email.smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(SenderError) as exc:
            send_email(
                to="owner@example.com",
                subject="Kubo digest",
                text_body="plain",
                html_body="<p>html</p>",
                smtp_config=cfg,
            )

    assert _TEST_PASSWORD not in str(exc.value)
    assert _TEST_PASSWORD not in repr(exc.value)


def test_missing_smtp_config_is_send_error() -> None:
    """SmtpConfig ausente → SenderError claro."""
    with pytest.raises(SenderError):
        send_email(
            to="owner@example.com",
            subject="Kubo digest",
            text_body="plain",
            html_body="<p>html</p>",
            smtp_config=None,  # type: ignore[arg-type]
        )


def test_injected_sender_is_called() -> None:
    """`sender` injetável permite teste puro sem tocar smtplib."""
    calls: list[dict[str, object]] = []

    def fake_sender(to: str, from_address: str, message: object) -> None:
        calls.append({"to": to, "from_address": from_address, "message": message})

    send_email(
        to="owner@example.com",
        subject="Kubo digest",
        text_body="plain",
        html_body="<p>html</p>",
        smtp_config=_config(),
        sender=fake_sender,
    )

    assert len(calls) == 1
    assert calls[0]["to"] == "owner@example.com"
    assert calls[0]["from_address"] == "kubo@example.com"


def test_email_smtp_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`email_smtp_config` monta SmtpConfig a partir das variáveis de ambiente padrão."""
    monkeypatch.setenv("KUBO_EMAIL_HOST", "smtp.example.com")
    monkeypatch.setenv("KUBO_EMAIL_PORT", "587")
    monkeypatch.setenv("KUBO_EMAIL_USER", "kubo@example.com")
    monkeypatch.setenv("KUBO_EMAIL_PASSWORD", _TEST_PASSWORD)  # pragma: allowlist secret
    monkeypatch.setenv("KUBO_EMAIL_FROM", "kubo@example.com")

    cfg = email_smtp_config()
    assert cfg is not None
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 587
    assert cfg.user == "kubo@example.com"
    assert cfg.password == _TEST_PASSWORD
    assert cfg.from_address == "kubo@example.com"


def test_email_smtp_config_returns_none_when_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se algum campo obrigatório falta, retorna None sem levantar."""
    for var in (
        "KUBO_EMAIL_HOST",
        "KUBO_EMAIL_PORT",
        "KUBO_EMAIL_USER",
        "KUBO_EMAIL_PASSWORD",
        "KUBO_EMAIL_FROM",
    ):
        monkeypatch.delenv(var, raising=False)

    assert email_smtp_config() is None
