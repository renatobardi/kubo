"""Worker `email-digest` sob contrato (ADR-0031) — unit puro.

Sem SurrealDB, sem rede. O worker atua sobre UM destino de e-mail; endereço e
config SMTP chegam pelo construtor (PII/segredo nunca em config/log/payload).
"""

from __future__ import annotations

import structlog

from kubo.distribution.email import SmtpConfig
from kubo.store.destinations import Destination
from kubo.workers.email_digest import EmailDigestConfig, EmailDigestWorker
from tests.workers._digest_fixtures import (
    _assert_no_novelty,
    _assert_send_failure,
    _assert_sends_digest,
    _assert_wrong_channel,
    _destination,
    _dispatch,
    _FakeCtx,
    _FakeKnowledge,
    _FakeSender,
    _view,
)

_TEST_PASSWORD = "super-secret-app-password"  # pragma: allowlist secret
_EMAIL_DEST = _destination(channel="email", address="owner@example.com")


def _smtp_config(password_value: str = "") -> SmtpConfig:
    return SmtpConfig(
        host="smtp.example.com",
        port=587,
        user="kubo@example.com",
        password=password_value,
        from_address="kubo@example.com",
    )


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
        base_url="https://kubo.test:3900",
        smtp_config=smtp_config,  # type: ignore[arg-type]
        email_sender=sender,
    )


def test_sends_digest_and_records_ok_dispatch() -> None:
    views = [_view("a", 0), _view("b", 10), _view("c", 5)]
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": views})
    sender = _FakeSender()
    result = _worker(_EMAIL_DEST, sender).run(_ctx(know))

    _assert_sends_digest(
        result,
        sender,
        channel="email",
        address="owner@example.com",
        expected_call={"to": "owner@example.com"},
    )


def test_no_novelty_sends_nothing() -> None:
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": []})
    sender = _FakeSender()
    result = _worker(_EMAIL_DEST, sender).run(_ctx(know))

    _assert_no_novelty(result, sender)


def test_send_failure_becomes_error_dispatch_without_exploding() -> None:
    views = [_view("a", 0), _view("b", 3)]
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": views})
    sender = _FakeSender(fail=True)
    result = _worker(_EMAIL_DEST, sender).run(_ctx(know))

    _assert_send_failure(result, sender, kind="email_send", watermark_minutes=3)


def test_missing_smtp_config_is_send_error_not_crash() -> None:
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    result = _worker(
        _destination(channel="email", address="owner@example.com"),
        sender,
        smtp_config=None,
    ).run(_ctx(know))

    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == "email_send"
    assert sender.calls == []


def test_address_and_password_never_appear_in_payload_or_repr() -> None:
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    destination = _destination(channel="email", address="owner.secret@example.com")
    cfg = _smtp_config(_TEST_PASSWORD)
    worker = EmailDigestWorker(
        destination=destination,
        base_url="https://kubo.test:3900",
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
    telegram = _destination(key="t1b2c3d4e5f67890", channel="telegram", address="42")
    know = _FakeKnowledge({"destination:t1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    result = _worker(telegram, sender).run(_ctx(know))

    _assert_wrong_channel(result, sender, kind="email_send")
