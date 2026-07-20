"""Worker `telegram-digest` sob contrato (ADR-0029 §2/§3) — unit puro.

Sem SurrealDB, sem rede. O worker atua sobre UM destino (canal Telegram), recebe
o endereço pelo construtor (PII, nunca na config/log/payload) e devolve um
`RunResult` com `DispatchPayload` ok/error — nunca explode.
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
from kubo.errors import SenderError
from kubo.store.destinations import Destination
from kubo.workers.digest import DigestConfig, TelegramDigestWorker

_NOW = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
_BASE = "https://kubo.test:3900"
_FAKE_TOKEN = "BOT-TOKEN"  # constante nomeada (evita S107 no default do fake)


def _view(key: str, minutes: int = 0) -> DigestView:
    return DigestView(
        id=f"distilled:{key}",
        title=f"Titulo {key}",
        summary=f"resumo {key}",
        created_at=_NOW + timedelta(minutes=minutes),
        entities=["OpenAI"],
    )


def _dest(address: str = "42") -> Destination:
    return Destination(
        id=RecordID("destination", "a1b2c3d4e5f67890"),
        name="Owner",
        kind="pessoa",
        channel="telegram",
        address=address,
        enabled=True,
        archived_at=None,
        dispatches=0,
    )


class _FakeKnowledge:
    """distilled_for_digest devolve a lista canned e registra chamadas."""

    def __init__(self, per_dest: dict[str, list[DigestView]]) -> None:
        self._per_dest = per_dest
        self.calls: list[tuple[str, int]] = []

    def items_to_distill(self, limit: int) -> list[Any]:  # não usado
        return []

    def distilled_for_digest(self, destination: str, limit: int) -> list[DigestView]:
        self.calls.append((destination, limit))
        return list(self._per_dest.get(destination, []))

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[Any]:  # não usado
        return []


@dataclass
class _Integration:
    secret: str | None


@dataclass
class _FakeCtx:
    config: DigestConfig
    integrations: dict[str, _Integration]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: None = None


class _FakeSender:
    """Registra as chamadas de envio; opcionalmente falha (SenderError)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    def __call__(self, *, token: str, chat_id: str, text: str) -> None:
        self.calls.append({"token": token, "chat_id": chat_id, "text": text})
        if self.fail:
            raise SenderError("Telegram respondeu HTTP 400")


def _ctx(knowledge: _FakeKnowledge, secret: str | None = _FAKE_TOKEN) -> _FakeCtx:
    return _FakeCtx(
        config=DigestConfig(),
        integrations={"telegram": _Integration(secret=secret)},
        knowledge=knowledge,
        logger=structlog.get_logger(),
    )


def _worker(destination: Destination, sender: _FakeSender) -> TelegramDigestWorker:
    return TelegramDigestWorker(destination=destination, base_url=_BASE, sender=sender)


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
    assert d.channel == "telegram"
    assert d.destination == "destination:a1b2c3d4e5f67890"
    assert d.item_count == 3
    assert set(d.items) == {"distilled:a", "distilled:b", "distilled:c"}
    assert d.watermark == _NOW + timedelta(minutes=10)  # max(created_at)
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == 3
    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == "BOT-TOKEN"
    assert sender.calls[0]["chat_id"] == "42"


def test_no_novelty_sends_nothing() -> None:
    """Destino sem novidade → nenhum dispatch, nenhum envio, run ok, new_distilled=0."""
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
    assert d.error is not None and d.error.kind == "telegram_send"
    assert d.watermark == _NOW + timedelta(minutes=3)
    assert result.error is not None and result.error.kind == "telegram_send"


def test_missing_token_is_send_error_not_crash() -> None:
    """Token ausente no ctx (least-privilege/env) → dispatch(error), não crash."""
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    result = _worker(_dest(), sender).run(_ctx(know, secret=None))
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert sender.calls == []  # nunca chega a enviar sem token


def test_address_never_appears_in_payload_config_or_repr() -> None:
    """ADR-0029 §3: o endereço (PII) viaja pelo construtor, nunca em config/payload/log."""
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    destination = _dest(address="55669999")
    result = TelegramDigestWorker(destination=destination, base_url=_BASE, sender=sender).run(
        _ctx(know)
    )

    assert "55669999" not in repr(destination)
    assert result.payloads
    d = _dispatch(result.payloads[0])
    assert "55669999" not in d.model_dump_json()
    # O sender recebe o chat_id, mas o payload/config/execução não o expõe.
    assert sender.calls[0]["chat_id"] == "55669999"


def test_non_telegram_destination_is_not_sent() -> None:
    """O worker de Telegram defensivamente não envia para outro canal."""
    email = Destination(
        id=RecordID("destination", "e1b2c3d4e5f67890"),
        name="Owner",
        kind="pessoa",
        channel="email",
        address="owner@example.com",
        enabled=True,
        archived_at=None,
        dispatches=0,
    )
    know = _FakeKnowledge({"destination:e1b2c3d4e5f67890": [_view("a")]})
    sender = _FakeSender()
    result = _worker(email, sender).run(_ctx(know))

    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == "telegram_send"
    assert sender.calls == []
