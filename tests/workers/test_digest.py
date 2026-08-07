"""Worker `telegram-digest` sob contrato (ADR-0029 §2/§3, ADR-0050) — unit puro.

Sem SurrealDB, sem rede. O worker atua sobre UM destino (canal Telegram), recebe
o endereço pelo construtor (PII, nunca na config/log/payload) e devolve um
`RunResult` com `DispatchPayload` ok/error — nunca explode.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from kubo.store.destinations import Destination
from kubo.workers.digest import DigestConfig, TelegramDigestWorker
from tests.workers._digest_fixtures import (
    _assert_send_failure,
    _assert_sends_digest,
    _assert_wrong_channel,
    _destination,
    _dispatch,
    _FakeCtx,
    _FakeKnowledge,
    _FakeSender,
    _selection,
    _view,
)

_FAKE_TOKEN = "BOT-TOKEN"


@dataclass
class _Integration:
    secret: str | None


def _ctx(knowledge: _FakeKnowledge, secret: str | None = _FAKE_TOKEN) -> _FakeCtx:
    return _FakeCtx(
        config=DigestConfig(),
        integrations={"telegram": _Integration(secret=secret)},
        knowledge=knowledge,
        logger=structlog.get_logger(),
    )


def _worker(destination: Destination, sender: _FakeSender) -> TelegramDigestWorker:
    return TelegramDigestWorker(
        destination=destination,
        base_url="https://kubo.test:3900",
        sender=sender,
    )


def test_sends_digest_and_records_ok_dispatch() -> None:
    sel = _selection([_view("a"), _view("b"), _view("c")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    _assert_sends_digest(
        result,
        sender,
        channel="telegram",
        address="42",
        expected_call={"token": "BOT-TOKEN", "chat_id": "42"},
    )
    d = _dispatch(result.payloads[0])
    assert set(d.items) == {"item:a", "item:b", "item:c"}


def test_empty_window_sends_warning_and_records_ok() -> None:
    sel = _selection(items=[], form="empty_window", total_publications=0)
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = _dispatch(result.payloads[0])
    assert d.status == "ok"
    assert d.item_count == 0
    assert d.items == []
    assert len(sender.calls) == 1  # aviso é enviado, não silêncio


def test_send_failure_becomes_error_dispatch_without_exploding() -> None:
    sel = _selection([_view("a"), _view("b")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender(fail=True)
    result = _worker(_destination(), sender).run(_ctx(know))

    _assert_send_failure(result, sender, kind="telegram_send", watermark_minutes=0)


def test_missing_token_is_send_error_not_crash() -> None:
    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know, secret=None))
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert sender.calls == []


def test_address_never_appears_in_payload_config_or_repr() -> None:
    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    destination = _destination(address="55669999")
    result = TelegramDigestWorker(
        destination=destination, base_url="https://kubo.test:3900", sender=sender
    ).run(_ctx(know))

    assert "55669999" not in repr(destination)
    assert result.payloads
    d = _dispatch(result.payloads[0])
    assert "55669999" not in d.model_dump_json()
    assert sender.calls[0]["chat_id"] == "55669999"


def test_non_telegram_destination_is_not_sent() -> None:
    email = _destination(key="e1b2c3d4e5f67890", channel="email", address="owner@example.com")
    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:e1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(email, sender).run(_ctx(know))

    _assert_wrong_channel(result, sender, kind="telegram_send")
