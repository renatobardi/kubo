"""Comportamento do adaptador concreto de knowledge que o runner injeta em todo
run (ADR-0013 §III). `GraphKnowledge` lê itens pendentes via store, atribui
refs OPACOS int (o worker nunca vê RecordID) e resolve ref->RecordID FORA do
Protocol `KnowledgeReader` — só o runner (ou o teste, aqui) chama `resolve`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.runtime.context import GraphKnowledge
from kubo.store import client, knowledge, migrations

pytestmark = pytest.mark.integration

_ADAPTER_DB = "test_knowledge_adapter"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_ADAPTER_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_ADAPTER_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_ADAPTER_DB};")


def test_items_to_distill_assigns_sequential_refs_and_resolves(db: Any) -> None:
    """2 itens pendentes -> items_to_distill(limit=10) devolve 2 ItemViews com
    refs 0 e 1; gk.resolve(0)/gk.resolve(1) devolvem os RecordIDs corretos."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = knowledge.upsert_item(
        db, source=source_id, external_id="a", content="conteúdo A", title="Título A"
    )
    item_b = knowledge.upsert_item(
        db, source=source_id, external_id="b", content="conteúdo B", title="Título B"
    )

    gk = GraphKnowledge(db)
    views = gk.items_to_distill(limit=10)

    assert len(views) == 2
    refs = {v.ref for v in views}
    assert refs == {0, 1}
    by_ref = {v.ref: v for v in views}
    resolved_a = gk.resolve(0)
    resolved_b = gk.resolve(1)
    assert {str(resolved_a), str(resolved_b)} == {str(item_a), str(item_b)}
    if resolved_a == item_a:
        assert by_ref[0].title == "Título A"
        assert by_ref[0].content == "conteúdo A"
        assert by_ref[1].title == "Título B"
        assert by_ref[1].content == "conteúdo B"
    else:
        assert by_ref[0].title == "Título B"
        assert by_ref[0].content == "conteúdo B"
        assert by_ref[1].title == "Título A"
        assert by_ref[1].content == "conteúdo A"


def test_resolve_unknown_ref_returns_none(db: Any) -> None:
    """resolve de um ref nunca atribuído devolve None — nunca levanta."""
    gk = GraphKnowledge(db)

    assert gk.resolve(999) is None


def test_refs_are_monotonic_across_two_calls(db: Any) -> None:
    """Com 3 itens pendentes e limit=2: a 1a chamada dá refs 0,1; a 2a dá refs
    2,3 (NUNCA reseta o contador). `items_to_distill` não consome/marca itens
    como destilados — como nada muda entre as chamadas, a 2a lê de novo os
    MESMOS 2 itens (ordem determinística por id), só que com refs novos; prova
    que o contador é por-instância e monotônico, não que o lote muda sozinho."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    knowledge.upsert_item(db, source=source_id, external_id="b", content="B")
    knowledge.upsert_item(db, source=source_id, external_id="c", content="C")

    gk = GraphKnowledge(db)
    first_batch = gk.items_to_distill(limit=2)
    second_batch = gk.items_to_distill(limit=2)

    assert {v.ref for v in first_batch} == {0, 1}
    assert {v.ref for v in second_batch} == {2, 3}  # monotônico: NÃO reseta pra 0

    # mesma ordem determinística -> o 1o ref de cada lote resolve ao mesmo item,
    # e idem para o 2o (a store não mudou nada entre as duas leituras).
    first_sorted = sorted(first_batch, key=lambda v: v.ref)
    second_sorted = sorted(second_batch, key=lambda v: v.ref)
    assert str(gk.resolve(first_sorted[0].ref)) == str(gk.resolve(second_sorted[0].ref))
    assert str(gk.resolve(first_sorted[1].ref)) == str(gk.resolve(second_sorted[1].ref))
    assert gk.resolve(first_sorted[0].ref) is not None
    assert gk.resolve(first_sorted[1].ref) is not None


def test_items_to_distill_excludes_already_distilled(db: Any) -> None:
    """Um item COM destilado não aparece em items_to_distill — o filtro vem de
    items_without_distilled; confirma a integração ponta a ponta."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    pending_item = knowledge.upsert_item(
        db, source=source_id, external_id="pending", content="pendente"
    )
    distilled_item = knowledge.upsert_item(
        db, source=source_id, external_id="distilled", content="já destilado"
    )
    knowledge.insert_distilled(db, item=distilled_item, summary="resumo", chunks=[])

    gk = GraphKnowledge(db)
    views = gk.items_to_distill(limit=10)

    assert len(views) == 1
    assert gk.resolve(views[0].ref) == pending_item
