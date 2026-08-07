"""Contrato de comportamento da store de `dispatch` + watermark (ADR-0015, ADR-0050).

Integração (SurrealDB real): watermark é datetime que faz round-trip pelo SDK.
Cobre: insert_dispatch (fato de entrega + items de auditoria como record<item>),
last_dispatch_watermark (só `ok` avança, por destino), list_dispatches.
A seleção por janela de publicação (items_for_digest) é testada em
test_digest_window.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, knowledge, migrations

pytestmark = pytest.mark.integration

_DISPATCH_DB = "test_dispatch"


def _dest(key: str) -> RecordID:
    """RecordID de destination para usar nos testes pós-cutover (KUBO-48)."""
    return RecordID("destination", key)


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_DISPATCH_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DISPATCH_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DISPATCH_DB};")


def test_insert_dispatch_records_the_delivery_fact(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """insert_dispatch grava destino/canal/status/watermark/item_count/items com
    sent_at automático; items são record<item> para auditoria (ADR-0050 §III)."""
    now = datetime.now(timezone.utc)
    item = _orphan_item(db, tenant_id, user_id, 0)
    rid = knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=1,
        items=[item],
    )
    row = db.query("SELECT * FROM $r;", {"r": rid})[0]
    assert row["destination"] == RecordID("destination", "owner-telegram")
    assert row["channel"] == "telegram"
    assert row["status"] == "ok"
    assert row["item_count"] == 1
    assert row["items"] == [item]
    assert row["sent_at"] is not None


def test_insert_dispatch_error_carries_structured_error(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """dispatch com status=error carrega o erro estruturado (FLEXIBLE) — visível em Envios."""
    now = datetime.now(timezone.utc)
    rid = knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="error",
        watermark=now,
        item_count=0,
        items=[],
        error={"kind": "telegram_http", "message": "400 bad request"},
    )
    row = db.query("SELECT * FROM $r;", {"r": rid})[0]
    assert row["status"] == "error"
    assert row["error"]["kind"] == "telegram_http"


def test_last_watermark_is_none_without_prior_dispatch(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Sem dispatch anterior daquele destino → None (sinal de bootstrap now-24h)."""
    assert (
        knowledge.last_dispatch_watermark(
            db, _dest("owner-telegram"), tenant_id=tenant_id, user_id=user_id
        )
        is None
    )


def test_last_watermark_only_ok_advances(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Só dispatch `ok` avança o watermark; um error POSTERIOR com watermark maior é ignorado."""
    early = datetime.now(timezone.utc) - timedelta(hours=2)
    late = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("d"),
        channel="telegram",
        status="ok",
        watermark=early,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("d"),
        channel="telegram",
        status="error",
        watermark=late,
        item_count=0,
        items=[],
    )
    assert (
        knowledge.last_dispatch_watermark(db, _dest("d"), tenant_id=tenant_id, user_id=user_id)
        == early
    )


def test_last_watermark_is_per_destination(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """O watermark é isolado por destino — telegram e e-mail não se cruzam."""
    tg = datetime.now(timezone.utc) - timedelta(hours=1)
    em = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("tg"),
        channel="telegram",
        status="ok",
        watermark=tg,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("em"),
        channel="email",
        status="ok",
        watermark=em,
        item_count=1,
        items=[],
    )
    assert (
        knowledge.last_dispatch_watermark(db, _dest("tg"), tenant_id=tenant_id, user_id=user_id)
        == tg
    )
    assert (
        knowledge.last_dispatch_watermark(db, _dest("em"), tenant_id=tenant_id, user_id=user_id)
        == em
    )


def _orphan_item(db: Any, tenant_id: RecordID, user_id: RecordID, seq: int) -> RecordID:
    """Item mínimo para o distilled derivar (derived_from exige endpoint existente)."""
    src = knowledge.upsert_source(
        db, tenant_id=tenant_id, user_id=user_id, kind="rss", canonical=f"wm-src::{seq}"
    )
    return knowledge.upsert_item(db, source=src, external_id=f"wm::{seq}", content="x", title="T")


# ── list_dispatches / count_dispatches (tela de Envios, 12.7) ──────────────────


def test_list_dispatches_most_recent_first(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """A tela de Envios lê os dispatches, mais recentes primeiro, com os campos de
    exibição (canal/destino/status/item_count/sent_at)."""
    now = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=3,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-email"),
        channel="email",
        status="error",
        watermark=now,
        item_count=0,
        items=[],
        error={"kind": "smtp_send", "message": "conn refused"},
    )
    rows = knowledge.list_dispatches(db, tenant_id=tenant_id, user_id=user_id, limit=50, start=0)
    assert len(rows) == 2
    # o de e-mail foi inserido depois → vem primeiro (sent_at DESC)
    first = rows[0]
    assert first.channel == "email"
    assert first.status == "error"
    assert first.error_kind == "smtp_send"
    assert first.destination == "owner-email"
    tele = rows[1]
    assert tele.channel == "telegram"
    assert tele.status == "ok"
    assert tele.item_count == 3


def test_list_dispatches_filters_by_query(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """A busca filtra por canal/destino/status (substring, case-insensitive)."""
    now = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-email"),
        channel="email",
        status="ok",
        watermark=now,
        item_count=1,
        items=[],
    )
    assert (
        len(
            knowledge.list_dispatches(
                db, tenant_id=tenant_id, user_id=user_id, limit=50, start=0, query="email"
            )
        )
        == 1
    )
    assert (
        len(
            knowledge.list_dispatches(
                db, tenant_id=tenant_id, user_id=user_id, limit=50, start=0, query="TELEGRAM"
            )
        )
        == 1
    )
    assert knowledge.count_dispatches(db, tenant_id=tenant_id, user_id=user_id, query="email") == 1
    assert knowledge.count_dispatches(db, tenant_id=tenant_id, user_id=user_id) == 2


# ── E1 (ADR-0016 §V): artifact isola o watermark do digest do de report ─────────


def test_report_dispatch_does_not_move_digest_watermark(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O bug latente que o E1 corrige: um dispatch de RELATÓRIO para o MESMO destino do
    digest (Telegram do dono) não pode mover o watermark do digest — senão o digest de
    amanhã pularia destilados em silêncio. O report entra com watermark None (forma de
    produção); o filtro `artifact='digest'` o exclui inteiro, então o watermark do digest
    permanece o do digest, nunca o None do report."""
    digest_wm = datetime.now(timezone.utc) - timedelta(hours=2)
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        artifact="digest",
        watermark=digest_wm,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        artifact="report",
        watermark=None,
        item_count=0,
        items=[],
    )
    assert (
        knowledge.last_dispatch_watermark(
            db, _dest("owner-telegram"), tenant_id=tenant_id, user_id=user_id
        )
        == digest_wm
    )


def test_gate_dispatch_does_not_move_digest_watermark(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """ADR-0018 §III: a notificação de GATE (novo `artifact="gate"`) grava um dispatch no
    mesmo destino do digest — e NÃO pode mover o watermark do digest (senão a notificação
    de gate faria o digest de amanhã pular destilados). Gate entra com watermark None; o
    filtro `artifact='digest'` o exclui, o watermark do digest permanece intacto."""
    digest_wm = datetime.now(timezone.utc) - timedelta(hours=2)
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        artifact="digest",
        watermark=digest_wm,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        artifact="gate",
        watermark=None,
        item_count=0,
        items=[],
    )
    assert (
        knowledge.last_dispatch_watermark(
            db, _dest("owner-telegram"), tenant_id=tenant_id, user_id=user_id
        )
        == digest_wm
    )
