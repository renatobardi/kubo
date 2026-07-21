"""Isolamento entre workers de digest por canal (KUBO-47, AC6).

Uma falha de e-mail (SenderError) não afeta a entrega no Telegram no mesmo sweep.
Teste unit puro, sem rede real — os senders são injetáveis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.contracts.models import DispatchPayload
from kubo.contracts.worker import DigestView
from kubo.distribution.email import SmtpConfig
from kubo.errors import SenderError
from kubo.store.destinations import Destination
from kubo.workers.digest import DigestConfig, TelegramDigestWorker
from kubo.workers.email_digest import EmailDigestConfig, EmailDigestWorker

_EMAIL_PASSWORD = "app-password"  # pragma: allowlist secret
_NOW = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
_BASE = "https://kubo.test:3900"


def _view(key: str, minutes: int = 0) -> DigestView:
    return DigestView(
        id=f"distilled:{key}",
        title=f"Titulo {key}",
        summary=f"resumo {key}",
        created_at=_NOW + timedelta(minutes=minutes),
        entities=["OpenAI"],
    )


def _dest(key: str, channel: str, address: str) -> Destination:
    return Destination(
        id=RecordID("destination", key),
        name=f"dest-{key}",
        kind="pessoa",
        channel=channel,
        address=address,
        enabled=True,
        archived_at=None,
        dispatches=0,
    )


class _FakeKnowledge:
    def __init__(self, per_dest: dict[str, list[DigestView]]) -> None:
        self._per_dest = per_dest

    def items_to_distill(self, limit: int) -> list[Any]:
        return []

    def distilled_for_digest(self, destination: str, limit: int) -> list[DigestView]:
        return list(self._per_dest.get(destination, []))

    def search_distilled(self, embedding: Any, k: int) -> list[Any]:
        return []


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
        destination=_dest("telegram1", "telegram", "42"),
        base_url=_BASE,
        sender=_FakeTelegramSender(),
    )
    email_worker = EmailDigestWorker(
        destination=_dest("email1", "email", "owner@example.com"),
        base_url=_BASE,
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
    tg = telegram_result.payloads[0]
    assert isinstance(tg, DispatchPayload) and tg.status == "ok" and tg.channel == "telegram"

    assert len(email_result.payloads) == 1
    em = email_result.payloads[0]
    assert isinstance(em, DispatchPayload) and em.status == "error" and em.channel == "email"
