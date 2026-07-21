"""Fixtures e helpers compartilhados pelos testes de workers de digest.

Módulo privado (não coletado pelo pytest) para evitar duplicação entre os testes
de TelegramDigestWorker e EmailDigestWorker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from surrealdb import RecordID

from kubo.contracts.models import DispatchPayload
from kubo.contracts.worker import DigestView
from kubo.errors import SenderError
from kubo.store.destinations import Channel, Destination
from kubo.workers._digest_common import DigestConfig

_NOW = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
_BASE = "https://kubo.test:3900"


def _view(key: str = "abc", minutes: int = 0) -> DigestView:
    return DigestView(
        id=f"distilled:{key}",
        title=f"Titulo {key}",
        summary=f"resumo {key}",
        created_at=_NOW + timedelta(minutes=minutes),
        entities=["OpenAI"],
    )


def _destination(
    key: str = "a1b2c3d4e5f67890",  # pragma: allowlist secret
    channel: Channel = "telegram",
    address: str = "42",
) -> Destination:
    return Destination(
        id=RecordID("destination", key),
        name="Owner",
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
    config: DigestConfig
    integrations: dict[str, Any]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: None = None


class _FakeSender:
    """Registra as chamadas de envio; opcionalmente falha (SenderError)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.fail:
            raise SenderError("send failure")


def _dispatch(payload: object) -> DispatchPayload:
    assert isinstance(payload, DispatchPayload)
    return payload


def _assert_sends_digest(
    result: Any,
    sender: _FakeSender,
    *,
    channel: str,
    address: str,
    expected_call: dict[str, object] | None = None,
    item_count: int = 3,
) -> None:
    assert len(result.payloads) == 1
    d = _dispatch(result.payloads[0])
    assert d.status == "ok"
    assert d.channel == channel
    assert d.destination == "destination:a1b2c3d4e5f67890"
    assert d.item_count == item_count
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == item_count
    assert len(sender.calls) == 1
    if expected_call:
        for key, value in expected_call.items():
            assert sender.calls[0][key] == value


def _assert_no_novelty(result: Any, sender: _FakeSender) -> None:
    assert result.payloads == []
    assert sender.calls == []
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == 0


def _assert_send_failure(
    result: Any,
    sender: _FakeSender,
    *,
    kind: str,
    watermark_minutes: int,
) -> None:
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == kind
    assert d.watermark == _NOW + timedelta(minutes=watermark_minutes)
    assert result.error is not None and result.error.kind == kind


def _assert_wrong_channel(
    result: Any,
    sender: _FakeSender,
    *,
    kind: str,
) -> None:
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == kind
    assert sender.calls == []
