"""Isolamento entre workers de digest por canal (KUBO-47, AC6).

Uma falha de e-mail (SenderError) não afeta a entrega no Telegram no mesmo sweep.
Teste unit puro, sem rede real — os senders são injetáveis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from kubo.distribution.email import SmtpConfig
from kubo.errors import SenderError
from kubo.workers.digest import DigestConfig, TelegramDigestWorker
from kubo.workers.email_digest import EmailDigestConfig, EmailDigestWorker
from tests.workers._digest_fixtures import (
    _destination,
    _dispatch,
    _FakeKnowledge,
    _view,
)

_EMAIL_PASSWORD = "app-password"  # pragma: allowlist secret


class _FakeTelegramSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, token: str, chat_id: str, text: str) -> None:
        self.calls.append({"token": token, "chat_id": chat_id, "text": text})


class _FakeEmailSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.fail:
            raise SenderError("SMTP respondeu 500")


@dataclass
class _FakeCtx:
    config: Any
    integrations: dict[str, Any]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: None = None


def test_email_failure_does_not_stop_telegram_delivery() -> None:
    """Falha de envio de e-mail (SenderError) não prejudica o Telegram."""
    views = {
        "destination:telegram1": [_view("a", 0), _view("b", 5)],
        "destination:email1": [_view("c", 3)],
    }
    knowledge = _FakeKnowledge(views)

    telegram_worker = TelegramDigestWorker(
        destination=_destination(key="telegram1", channel="telegram", address="42"),
        base_url="https://kubo.test:3900",
        sender=_FakeTelegramSender(),
    )
    email_worker = EmailDigestWorker(
        destination=_destination(key="email1", channel="email", address="owner@example.com"),
        base_url="https://kubo.test:3900",
        smtp_config=SmtpConfig(
            host="smtp.example.com",
            port=587,
            user="kubo@example.com",
            password=_EMAIL_PASSWORD,
            from_address="kubo@example.com",
        ),
        email_sender=_FakeEmailSender(fail=True),
    )

    logger = structlog.get_logger()
    telegram_result = telegram_worker.run(
        _FakeCtx(
            config=DigestConfig(),
            integrations={"telegram": type("I", (), {"secret": "BOT-TOKEN"})},
            knowledge=knowledge,
            logger=logger,
        )
    )
    email_result = email_worker.run(
        _FakeCtx(
            config=EmailDigestConfig(),
            integrations={},
            knowledge=knowledge,
            logger=logger,
        )
    )

    assert len(telegram_result.payloads) == 1
    tg = _dispatch(telegram_result.payloads[0])
    assert tg.status == "ok" and tg.channel == "telegram"

    assert len(email_result.payloads) == 1
    em = _dispatch(email_result.payloads[0])
    assert em.status == "error" and em.channel == "email"
