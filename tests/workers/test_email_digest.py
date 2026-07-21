"""Worker `email-digest` sob contrato (ADR-0031) — unit puro.

Sem SurrealDB, sem rede. O worker atua sobre UM destino de e-mail; endereço e
config SMTP chegam pelo construtor (PII/segredo nunca em config/log/payload).
"""

from __future__ import annotations

from collections.abc import Sequence
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
from kubo.workers.email_digest import EmailDigestConfig, EmailDigestWorker

_TEST_PASSWORD = "super-secret-app-password"  # pragma: allowlist secret
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


def _dest(address: str = "owner@example.com") -> Destination:
    return Destination(
        id=RecordID("destination", "a1b2c3d4e5f67890"),
        name="Owner",
        kind="pessoa",
        channel="email",
        address=address,
        enabled=True,
        archived_at=None,
        dispatches=0,
    )


def _smtp_config(password_value: str = "") -> SmtpConfig:
    return SmtpConfig(
        host="smtp.example.com",
        port=587,
        user="kubo@example.com",
        password=password_value,
        from_address="kubo@example.com",
    )


class _FakeKnowledge:
    def __init__(self, per_dest: dict[str, list[DigestView]]) -> None:
        self._per_dest = per_dest
        self.calls: list[tuple[str, int]] = []

    def items_to_distill(self, limit: int) -> list[Any]:
        return []

    def distilled_for_digest(self, destination: str, limit: int) -> list[DigestView]:
        self.calls.append((destination, limit))
        return list(self._per_dest.get(destination, []))

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[Any]:
        return []


@dataclass
class _FakeCtx:
    config: EmailDigestConfig
    integrations: dict[str, Any]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: None = None


class _FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.fail:
            raise SenderError("SMTP respondeu 500")


def _ctx(knowledge: _FakeKnowledge) -> _FakeCtx:
    return _FakeCtx(
        config=EmailDigestConfig(),
        integrations={},
        knowledge=knowledge,
        logger=structlog.get_logger(),
    )


_MISSING = object()


def _worker(
    destination: Destination,
    sender: _FakeSender,
    smtp_config: SmtpConfig | None | object = _MISSING,
) -> EmailDigestWorker:
    if smtp_config is _MISSING:
        smtp_config = _smtp_config()
    return EmailDigestWorker(
        destination=destination,
        base_url=_BASE,
        smtp_config=smtp_config,  # type: ignore[arg-type]
        email_sender=sender,
    )


def _dispatch(payload: object) -> DispatchPayload:
    assert isinstance(payload, DispatchPayload)
    return payload


def test_sends_digest_and_records_ok_dispatch() -> None:
    """Destino com 3 novidades → 1 DispatchPayload(ok), sender chamado, watermark=max."""
    views = [_view("a", 0), _view("b", 10), _view("c", 5)]
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": views})
    sender = _FakeSender()
    result = _worker(_dest(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = _dispatch(result.payloads[0])
    assert d.status == "ok"
    assert d.channel == "email"
    assert d.destination == "destination:a1b2c3d4e5f67890"
    assert d.item_count == 3
    assert set(d.items) == {"distilled:a", "distilled:b", "distilled:c"}
    assert d.watermark == _NOW + timedelta(minutes=10)
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == 3
    assert len(sender.calls) == 1
    assert sender.calls[0]["to"] == "owner@example.com"


def test_no_novelty_sends_nothing() -> None:
    """Destino sem novidade → nenhum dispatch, nenhum envio, run ok."""
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": []})
    sender = _FakeSender()
    result = _worker(_dest(), sender).run(_ctx(know))

    assert result.payloads == []
    assert sender.calls == []
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == 0


def test_send_failure_becomes_error_dispatch_without_exploding() -> None:
    """Falha de envio → DispatchPayload(error) estruturado; run não explode."""
    views = [_view("a", 0), _view("b", 3)]
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": views})
    sender = _FakeSender(fail=True)
    result = _worker(_dest(), sender).run(_ctx(know))

    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == "email_send"
    assert d.watermark == _NOW + timedelta(minutes=3)
    assert result.error is not None and result.error.kind == "email_send"


def test_missing_smtp_config_is_send_error_not_crash() -> None:
    """Config SMTP ausente → dispatch(error), não crash."""
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    result = _worker(_dest(), sender, smtp_config=None).run(_ctx(know))

    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == "email_send"
    assert sender.calls == []


def test_address_and_password_never_appear_in_payload_or_repr() -> None:
    """ADR-0031: endereço e senha viajam pelo construtor, nunca em config/payload/log."""
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    destination = _dest(address="owner.secret@example.com")
    cfg = _smtp_config(_TEST_PASSWORD)
    worker = EmailDigestWorker(
        destination=destination,
        base_url=_BASE,
        smtp_config=cfg,
        email_sender=sender,
    )
    result = worker.run(_ctx(know))

    assert "owner.secret@example.com" not in repr(destination)
    assert _TEST_PASSWORD not in repr(cfg)
    assert result.payloads
    d = _dispatch(result.payloads[0])
    assert "owner.secret@example.com" not in d.model_dump_json()
    assert _TEST_PASSWORD not in d.model_dump_json()
    assert "owner.secret@example.com" not in repr(worker)
    assert _TEST_PASSWORD not in repr(worker)
    assert sender.calls[0]["to"] == "owner.secret@example.com"


def test_non_email_destination_is_not_sent() -> None:
    """O worker de e-mail defensivamente não envia para outro canal."""
    telegram = Destination(
        id=RecordID("destination", "t1b2c3d4e5f67890"),
        name="Owner",
        kind="pessoa",
        channel="telegram",
        address="42",
        enabled=True,
        archived_at=None,
        dispatches=0,
    )
    know = _FakeKnowledge({"destination:t1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    result = _worker(telegram, sender).run(_ctx(know))

    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == "email_send"
    assert sender.calls == []
