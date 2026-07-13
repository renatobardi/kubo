"""Contrato do seam de retrieval da analista: `search_distilled` (integração, KNN real).

Cobre: KNN sobre chunks resolvido ao distilled, dedup por distilled (dois chunks do
mesmo distilled = uma citação), ordenação por proximidade, e a resolução de título
(via `derived_from`→item) + summary que a analista usa para citar (ADR-0016 §III).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, migrations
from kubo.store.knowledge import (
    Chunk,
    insert_distilled,
    search_distilled,
    upsert_item,
    upsert_source,
)

pytestmark = pytest.mark.integration

_DB = "test_search_distilled"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _vec(axis: int) -> list[float]:
    """Vetor unitário 768-d ao longo de `axis` — separação limpa entre distilled no KNN."""
    v = [0.0] * 768
    v[axis] = 1.0
    return v


def _distilled(db: Any, *, title: str, summary: str, vectors: list[list[float]]) -> RecordID:
    """Cria source+item (com título) e um distilled com um chunk por vetor dado."""
    src = upsert_source(db, kind="rss", canonical=f"src::{title}")
    item = upsert_item(db, source=src, external_id=f"ext::{title}", content="x", title=title)
    chunks = [
        Chunk(text=summary, seq=i, embedding=v, model="m", dim=768, task_type="SEMANTIC_SIMILARITY")
        for i, v in enumerate(vectors)
    ]
    return insert_distilled(db, item=item, summary=summary, chunks=chunks)


def test_search_returns_closest_first_with_title_and_summary(db: Any) -> None:
    """A busca resolve título (via derived_from) + summary e ordena por proximidade:
    query no eixo 0 traz o distilled do eixo 0 primeiro."""
    d0 = _distilled(db, title="Rust", summary="sobre Rust", vectors=[_vec(0)])
    _distilled(db, title="Python", summary="sobre Python", vectors=[_vec(1)])

    docs = search_distilled(db, embedding=_vec(0), k=5)

    assert docs[0].id == d0
    assert docs[0].title == "Rust"
    assert docs[0].summary == "sobre Rust"


def test_search_dedups_by_distilled(db: Any) -> None:
    """Dois chunks do MESMO distilled não viram duas citações — dedup por distilled."""
    d0 = _distilled(db, title="Rust", summary="sobre Rust", vectors=[_vec(0), _vec(2)])

    docs = search_distilled(db, embedding=_vec(0), k=10)

    assert [d.id for d in docs] == [d0]
