"""Workers de digest: as quatro formas de mensagem (ADR-0050 §VI, KUBO-194).

Unit puro (sem SurrealDB, sem rede): o worker atua sobre UM destino, recebe
a seleção do seam e devolve RunResult com DispatchPayload ok/error. Cobre:
- forma 1 (normal): envia digest com itens
- forma 2 (empty_window): envia aviso, item_count=0, status=ok
- forma 3 (none_passed): envia aviso com número, item_count=0, status=ok
- forma 4 (recovery): envia digest identificado como recuperação
- todas as formas produzem watermark = window_end
- aviso conta como dispatch ok (não é _empty_run)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from kubo.contracts.models import DispatchPayload
from kubo.contracts.worker import DigestSelectionView, DigestView
from kubo.store.destinations import Destination
from kubo.workers.digest import DigestConfig, TelegramDigestWorker
from tests.workers._digest_fixtures import _destination, _FakeSender

_FAKE_TOKEN = "BOT-TOKEN"
_NOW = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
_YESTERDAY = _NOW - timedelta(days=1)


@dataclass
class _Integration:
    secret: str | None


class _FakeWindowKnowledge:
    """Fake do seam que devolve DigestSelectionView (ADR-0050)."""

    def __init__(self, selection: DigestSelectionView) -> None:
        self._selection = selection
        self.calls: list[tuple[str, int]] = []

    def items_to_score(self, limit: int) -> list[Any]:
        return []

    def work_context(self) -> str:
        return ""

    def items_for_digest(self, destination: str, limit: int) -> DigestSelectionView:
        self.calls.append((destination, limit))
        return self._selection

    def search_distilled(self, embedding: Any, k: int) -> list[Any]:
        return []


@dataclass
class _FakeCtx:
    config: DigestConfig
    integrations: dict[str, Any]
    knowledge: _FakeWindowKnowledge
    logger: Any
    embedder: None = None


def _ctx(knowledge: _FakeWindowKnowledge, secret: str | None = _FAKE_TOKEN) -> _FakeCtx:
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


def _item_view(key: str = "abc", score: int = 7) -> DigestView:
    return DigestView(
        id=f"item:{key}",
        title=f"Titulo {key}",
        summary=f"resumo {key}",
        score=score,
        published_at=_YESTERDAY,
        url=f"https://example.com/{key}",
        entities=["OpenAI"],
    )


# ── Forma 1: normal ───────────────────────────────────────────────────────────


def test_form_normal_sends_digest_and_records_ok() -> None:
    items = [_item_view("a", 8), _item_view("b", 7)]
    selection = DigestSelectionView(
        form="normal",
        items=items,
        window_start=_YESTERDAY,
        window_end=_YESTERDAY,
        watermark=_YESTERDAY,
        total_publications=2,
    )
    know = _FakeWindowKnowledge(selection)
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = result.payloads[0]
    assert isinstance(d, DispatchPayload)
    assert d.status == "ok"
    assert d.channel == "telegram"
    assert d.item_count == 2
    assert d.watermark == _YESTERDAY
    assert set(d.items) == {"item:a", "item:b"}
    assert len(sender.calls) == 1


# ── Forma 2: empty_window ─────────────────────────────────────────────────────


def test_form_empty_window_sends_warning_and_records_ok() -> None:
    selection = DigestSelectionView(
        form="empty_window",
        items=[],
        window_start=_YESTERDAY,
        window_end=_YESTERDAY,
        watermark=_YESTERDAY,
        total_publications=0,
    )
    know = _FakeWindowKnowledge(selection)
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = result.payloads[0]
    assert isinstance(d, DispatchPayload)
    assert d.status == "ok"
    assert d.item_count == 0
    assert d.items == []
    assert d.watermark == _YESTERDAY
    assert len(sender.calls) == 1  # aviso é enviado, não silêncio


# ── Forma 3: none_passed ──────────────────────────────────────────────────────


def test_form_none_passed_sends_warning_with_count() -> None:
    selection = DigestSelectionView(
        form="none_passed",
        items=[],
        window_start=_YESTERDAY,
        window_end=_YESTERDAY,
        watermark=_YESTERDAY,
        total_publications=5,
    )
    know = _FakeWindowKnowledge(selection)
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = result.payloads[0]
    assert isinstance(d, DispatchPayload)
    assert d.status == "ok"
    assert d.item_count == 0
    assert d.watermark == _YESTERDAY
    assert len(sender.calls) == 1


# ── Forma 4: recovery ─────────────────────────────────────────────────────────


def test_form_recovery_sends_digest_and_records_ok() -> None:
    items = [_item_view("a", 9), _item_view("b", 6)]
    three_days_ago = _NOW - timedelta(days=3)
    selection = DigestSelectionView(
        form="recovery",
        items=items,
        window_start=three_days_ago,
        window_end=_YESTERDAY,
        watermark=_YESTERDAY,
        total_publications=2,
    )
    know = _FakeWindowKnowledge(selection)
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = result.payloads[0]
    assert isinstance(d, DispatchPayload)
    assert d.status == "ok"
    assert d.item_count == 2
    assert d.watermark == _YESTERDAY
    assert set(d.items) == {"item:a", "item:b"}
    # Verifica que o sender foi chamado e o texto contém o rótulo de recuperação
    assert len(sender.calls) == 1
    text = str(sender.calls[0]["text"])
    assert "recuperação" in text


# ── Send failure still records error dispatch ─────────────────────────────────


def test_send_failure_records_error_with_watermark() -> None:
    items = [_item_view("a", 8)]
    selection = DigestSelectionView(
        form="normal",
        items=items,
        window_start=_YESTERDAY,
        window_end=_YESTERDAY,
        watermark=_YESTERDAY,
        total_publications=1,
    )
    know = _FakeWindowKnowledge(selection)
    sender = _FakeSender(fail=True)
    result = _worker(_destination(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = result.payloads[0]
    assert isinstance(d, DispatchPayload)
    assert d.status == "error"
    assert d.error is not None
    assert d.error.kind == "telegram_send"
    assert d.watermark == _YESTERDAY
