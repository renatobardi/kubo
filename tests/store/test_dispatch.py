"""Contrato de comportamento da store de `dispatch` + seleção de digest (ADR-0015).

Integração (SurrealDB real): watermark é datetime que faz round-trip pelo SDK e
volta a comparar `created_at > $wm` — o teste que o advisor exigiu como o mais
valioso da sessão. Cobre: insert_dispatch (fato de entrega + items de auditoria),
last_dispatch_watermark (só `ok` avança, por destino), distilled_for_digest
(bootstrap now-24h, seleção `> watermark`, título+entidades, limit).
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


def _distilled_at(
    db: Any,
    *,
    summary: str,
    created_at: datetime,
    title: str | None = None,
    entities: tuple[str, ...] = (),
) -> RecordID:
    """Cria um distilled com `created_at` explícito (READONLY aceita no CREATE),
    opcionalmente com um item de origem (para o título via derived_from) e
    entidades citadas (via mentions). Helper de teste — queries com literais
    internos."""
    did = db.query(
        "CREATE distilled SET summary = $s, created_at = $c;",
        {"s": summary, "c": created_at},
    )[0]["id"]
    if title is not None:
        src = knowledge.upsert_source(db, kind="rss", canonical=f"src::{title}")
        item = knowledge.upsert_item(
            db, source=src, external_id=f"ext::{title}", content="x", title=title
        )
        db.query("RELATE $d->derived_from->$i;", {"d": did, "i": item})
    for name in entities:
        ent = knowledge.get_or_create_entity(db, name=name)
        db.query("RELATE $d->mentions->$e;", {"d": did, "e": ent})
    return did


def test_insert_dispatch_records_the_delivery_fact(db: Any) -> None:
    """insert_dispatch grava destino/canal/status/watermark/item_count/items com
    sent_at automático; items são record<distilled> para auditoria."""
    now = datetime.now(timezone.utc)
    d1 = _distilled_at(db, summary="a", created_at=now)
    rid = knowledge.insert_dispatch(
        db,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=1,
        items=[d1],
    )
    row = db.query("SELECT * FROM $r;", {"r": rid})[0]
    assert row["destination"] == RecordID("destination", "owner-telegram")
    assert row["channel"] == "telegram"
    assert row["status"] == "ok"
    assert row["item_count"] == 1
    assert row["items"] == [d1]
    assert row["sent_at"] is not None


def test_insert_dispatch_error_carries_structured_error(db: Any) -> None:
    """dispatch com status=error carrega o erro estruturado (FLEXIBLE) — visível em Envios."""
    now = datetime.now(timezone.utc)
    rid = knowledge.insert_dispatch(
        db,
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


def test_last_watermark_is_none_without_prior_dispatch(db: Any) -> None:
    """Sem dispatch anterior daquele destino → None (sinal de bootstrap now-24h)."""
    assert knowledge.last_dispatch_watermark(db, _dest("owner-telegram")) is None


def test_last_watermark_only_ok_advances(db: Any) -> None:
    """Só dispatch `ok` avança o watermark; um error POSTERIOR com watermark maior é ignorado."""
    early = datetime.now(timezone.utc) - timedelta(hours=2)
    late = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        destination=_dest("d"),
        channel="telegram",
        status="ok",
        watermark=early,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        destination=_dest("d"),
        channel="telegram",
        status="error",
        watermark=late,
        item_count=0,
        items=[],
    )
    assert knowledge.last_dispatch_watermark(db, _dest("d")) == early


def test_last_watermark_is_per_destination(db: Any) -> None:
    """O watermark é isolado por destino — telegram e e-mail não se cruzam."""
    tg = datetime.now(timezone.utc) - timedelta(hours=1)
    em = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        destination=_dest("tg"),
        channel="telegram",
        status="ok",
        watermark=tg,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        destination=_dest("em"),
        channel="email",
        status="ok",
        watermark=em,
        item_count=1,
        items=[],
    )
    assert knowledge.last_dispatch_watermark(db, _dest("tg")) == tg
    assert knowledge.last_dispatch_watermark(db, _dest("em")) == em


def test_digest_bootstrap_excludes_legado_older_than_24h(db: Any) -> None:
    """Sem dispatch anterior, o bootstrap now-24h NÃO despeja o legado: distilled de
    30h atrás fica de fora; o de 1h atrás entra."""
    now = datetime.now(timezone.utc)
    _distilled_at(db, summary="legado", created_at=now - timedelta(hours=30))
    fresh = _distilled_at(db, summary="fresco", created_at=now - timedelta(hours=1))
    views = knowledge.distilled_for_digest(db, destination=_dest("owner-telegram"), limit=50)
    assert [v.id for v in views] == [fresh]


def test_digest_selects_only_newer_than_watermark(db: Any) -> None:
    """Depois de um dispatch ok, só distilled com created_at > watermark entram."""
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    old = _distilled_at(db, summary="antigo", created_at=base)
    knowledge.insert_dispatch(
        db,
        destination=_dest("d"),
        channel="telegram",
        status="ok",
        watermark=base,
        item_count=1,
        items=[old],
    )
    newer = _distilled_at(db, summary="novo", created_at=base + timedelta(minutes=30))
    views = knowledge.distilled_for_digest(db, destination=_dest("d"), limit=50)
    assert [v.id for v in views] == [newer]


def test_digest_view_carries_title_entities_and_datetime(db: Any) -> None:
    """DigestView traz título (via derived_from→item), entidades (via mentions) e
    created_at como datetime (alimenta o max() do watermark)."""
    now = datetime.now(timezone.utc)
    did = _distilled_at(
        db, summary="resumo", created_at=now, title="OpenAI lança X", entities=("OpenAI", "GPT")
    )
    views = knowledge.distilled_for_digest(db, destination=_dest("new"), limit=50)
    assert len(views) == 1
    v = views[0]
    assert v.id == did
    assert v.summary == "resumo"
    assert v.title == "OpenAI lança X"
    assert set(v.entities) == {"OpenAI", "GPT"}
    assert isinstance(v.created_at, datetime)


def test_digest_respects_limit_and_orders_by_created_at(db: Any) -> None:
    """distilled_for_digest respeita o limit e ordena por created_at ascendente."""
    now = datetime.now(timezone.utc)
    a = _distilled_at(db, summary="a", created_at=now - timedelta(minutes=3))
    b = _distilled_at(db, summary="b", created_at=now - timedelta(minutes=2))
    _distilled_at(db, summary="c", created_at=now - timedelta(minutes=1))
    views = knowledge.distilled_for_digest(db, destination=_dest("new"), limit=2)
    assert [v.id for v in views] == [a, b]


def test_watermark_round_trip_closes_the_loop(db: Any) -> None:
    """O teste-âncora do advisor: enviar um conjunto, gravar watermark=max(created_at)
    das linhas devolvidas, e o próximo digest vem VAZIO (nada mais novo).

    Semeia via `insert_distilled` (created_at = `time::now()` do SERVIDOR, precisão
    de ns) — NÃO um datetime Python (que já vem truncado a μs). É essa a semente que
    expõe a armadilha de precisão do round-trip: se o watermark μs fosse comparado
    com o created_at ns cru, o último item reenviaria (bola de neve)."""
    for i in range(3):
        knowledge.insert_distilled(db, item=_orphan_item(db, i), summary=f"item {i}", chunks=[])
    picked = knowledge.distilled_for_digest(db, destination=_dest("d"), limit=50)
    assert len(picked) == 3
    watermark = max(v.created_at for v in picked)
    knowledge.insert_dispatch(
        db,
        destination=_dest("d"),
        channel="telegram",
        status="ok",
        watermark=watermark,
        item_count=len(picked),
        items=[v.id for v in picked],
    )
    assert knowledge.distilled_for_digest(db, destination=_dest("d"), limit=50) == []


def _orphan_item(db: Any, seq: int) -> RecordID:
    """Item mínimo para o distilled derivar (derived_from exige endpoint existente)."""
    src = knowledge.upsert_source(db, kind="rss", canonical=f"wm-src::{seq}")
    return knowledge.upsert_item(db, source=src, external_id=f"wm::{seq}", content="x", title="T")


# ── list_dispatches / count_dispatches (tela de Envios, 12.7) ──────────────────


def test_list_dispatches_most_recent_first(db: Any) -> None:
    """A tela de Envios lê os dispatches, mais recentes primeiro, com os campos de
    exibição (canal/destino/status/item_count/sent_at)."""
    now = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=3,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        destination=_dest("owner-email"),
        channel="email",
        status="error",
        watermark=now,
        item_count=0,
        items=[],
        error={"kind": "smtp_send", "message": "conn refused"},
    )
    rows = knowledge.list_dispatches(db, limit=50, start=0)
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


def test_list_dispatches_filters_by_query(db: Any) -> None:
    """A busca filtra por canal/destino/status (substring, case-insensitive)."""
    now = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        destination=_dest("owner-email"),
        channel="email",
        status="ok",
        watermark=now,
        item_count=1,
        items=[],
    )
    assert len(knowledge.list_dispatches(db, limit=50, start=0, query="email")) == 1
    assert len(knowledge.list_dispatches(db, limit=50, start=0, query="TELEGRAM")) == 1
    assert knowledge.count_dispatches(db, query="email") == 1
    assert knowledge.count_dispatches(db) == 2


# ── E1 (ADR-0016 §V): artifact isola o watermark do digest do de report ─────────


def test_report_dispatch_does_not_move_digest_watermark(db: Any) -> None:
    """O bug latente que o E1 corrige: um dispatch de RELATÓRIO para o MESMO destino do
    digest (Telegram do dono) não pode mover o watermark do digest — senão o digest de
    amanhã pularia destilados em silêncio. O report entra com watermark None (forma de
    produção); o filtro `artifact='digest'` o exclui inteiro, então o watermark do digest
    permanece o do digest, nunca o None do report."""
    digest_wm = datetime.now(timezone.utc) - timedelta(hours=2)
    knowledge.insert_dispatch(
        db,
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
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        artifact="report",
        watermark=None,
        item_count=0,
        items=[],
    )
    assert knowledge.last_dispatch_watermark(db, _dest("owner-telegram")) == digest_wm


def test_gate_dispatch_does_not_move_digest_watermark(db: Any) -> None:
    """ADR-0018 §III: a notificação de GATE (novo `artifact="gate"`) grava um dispatch no
    mesmo destino do digest — e NÃO pode mover o watermark do digest (senão a notificação
    de gate faria o digest de amanhã pular destilados). Gate entra com watermark None; o
    filtro `artifact='digest'` o exclui, o watermark do digest permanece intacto."""
    digest_wm = datetime.now(timezone.utc) - timedelta(hours=2)
    knowledge.insert_dispatch(
        db,
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
        destination=_dest("owner-telegram"),
        channel="telegram",
        status="ok",
        artifact="gate",
        watermark=None,
        item_count=0,
        items=[],
    )
    assert knowledge.last_dispatch_watermark(db, _dest("owner-telegram")) == digest_wm
