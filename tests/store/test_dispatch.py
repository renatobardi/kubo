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
        destination="owner-telegram",
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=1,
        items=[d1],
    )
    row = db.query("SELECT * FROM $r;", {"r": rid})[0]
    assert row["destination"] == "owner-telegram"
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
        destination="owner-telegram",
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
    assert knowledge.last_dispatch_watermark(db, "owner-telegram") is None


def test_last_watermark_only_ok_advances(db: Any) -> None:
    """Só dispatch `ok` avança o watermark; um error POSTERIOR com watermark maior é ignorado."""
    early = datetime.now(timezone.utc) - timedelta(hours=2)
    late = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        destination="d",
        channel="telegram",
        status="ok",
        watermark=early,
        item_count=1,
        items=[],
    )
    knowledge.insert_dispatch(
        db,
        destination="d",
        channel="telegram",
        status="error",
        watermark=late,
        item_count=0,
        items=[],
    )
    assert knowledge.last_dispatch_watermark(db, "d") == early


def test_last_watermark_is_per_destination(db: Any) -> None:
    """O watermark é isolado por destino — telegram e e-mail não se cruzam."""
    tg = datetime.now(timezone.utc) - timedelta(hours=1)
    em = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db, destination="tg", channel="telegram", status="ok", watermark=tg, item_count=1, items=[]
    )
    knowledge.insert_dispatch(
        db, destination="em", channel="email", status="ok", watermark=em, item_count=1, items=[]
    )
    assert knowledge.last_dispatch_watermark(db, "tg") == tg
    assert knowledge.last_dispatch_watermark(db, "em") == em


def test_digest_bootstrap_excludes_legado_older_than_24h(db: Any) -> None:
    """Sem dispatch anterior, o bootstrap now-24h NÃO despeja o legado: distilled de
    30h atrás fica de fora; o de 1h atrás entra."""
    now = datetime.now(timezone.utc)
    _distilled_at(db, summary="legado", created_at=now - timedelta(hours=30))
    fresh = _distilled_at(db, summary="fresco", created_at=now - timedelta(hours=1))
    views = knowledge.distilled_for_digest(db, destination="owner-telegram", limit=50)
    assert [v.id for v in views] == [fresh]


def test_digest_selects_only_newer_than_watermark(db: Any) -> None:
    """Depois de um dispatch ok, só distilled com created_at > watermark entram."""
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    old = _distilled_at(db, summary="antigo", created_at=base)
    knowledge.insert_dispatch(
        db,
        destination="d",
        channel="telegram",
        status="ok",
        watermark=base,
        item_count=1,
        items=[old],
    )
    newer = _distilled_at(db, summary="novo", created_at=base + timedelta(minutes=30))
    views = knowledge.distilled_for_digest(db, destination="d", limit=50)
    assert [v.id for v in views] == [newer]


def test_digest_view_carries_title_entities_and_datetime(db: Any) -> None:
    """DigestView traz título (via derived_from→item), entidades (via mentions) e
    created_at como datetime (alimenta o max() do watermark)."""
    now = datetime.now(timezone.utc)
    did = _distilled_at(
        db, summary="resumo", created_at=now, title="OpenAI lança X", entities=("OpenAI", "GPT")
    )
    views = knowledge.distilled_for_digest(db, destination="new", limit=50)
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
    views = knowledge.distilled_for_digest(db, destination="new", limit=2)
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
    picked = knowledge.distilled_for_digest(db, destination="d", limit=50)
    assert len(picked) == 3
    watermark = max(v.created_at for v in picked)
    knowledge.insert_dispatch(
        db,
        destination="d",
        channel="telegram",
        status="ok",
        watermark=watermark,
        item_count=len(picked),
        items=[v.id for v in picked],
    )
    assert knowledge.distilled_for_digest(db, destination="d", limit=50) == []


def _orphan_item(db: Any, seq: int) -> RecordID:
    """Item mínimo para o distilled derivar (derived_from exige endpoint existente)."""
    src = knowledge.upsert_source(db, kind="rss", canonical=f"wm-src::{seq}")
    return knowledge.upsert_item(db, source=src, external_id=f"wm::{seq}", content="x", title="T")
