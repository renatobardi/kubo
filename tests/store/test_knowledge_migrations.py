"""Contrato do schema de conhecimento — migrations 0001/0002 (integração, SurrealDB).

Estes testes travam as decisões de DDL validadas pelo advisor (plano 0003 §3.1.5):
estrutura de tabelas/arestas, índice HNSW, FLEXIBLE materializado, arestas ENFORCED,
timestamps READONLY. São o guarda de regressão do schema, independente da store.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.store import client, migrations

pytestmark = pytest.mark.integration

_KNOWLEDGE_DB = "test_knowledge_migrations"

_TABLES = {
    "source",
    "item",
    "distilled",
    "chunk",
    "entity",
    "run",
    "dispatch",
    # 0005 (execução, ADR-0016): flow/task/persona/deliverable.
    "flow",
    "task",
    "persona",
    "deliverable",
}
_EDGES = {
    "from_source",
    "derived_from",
    "mentions",
    "chunk_of",
    "produced_by",
    "collected_by",
    "relates_to",
    # 0005 (execução, ADR-0016): arestas de flow/task.
    "belongs_to",
    "assigned_to",
    "produces",
    "consults",
}


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — migrations aplicam do zero."""
    cfg = replace(client.config(), database=_KNOWLEDGE_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_KNOWLEDGE_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_KNOWLEDGE_DB};")


def test_apply_is_idempotent(db: Any) -> None:
    """As duas migrations aplicaram (fixture); reexecutar é no-op e o registro persiste."""
    assert migrations.apply_migrations(db) == []
    recorded = {r["name"] for r in db.query("SELECT name FROM migration;")}
    assert recorded == {
        "0001_knowledge_schema.surql",
        "0002_hnsw_index.surql",
        "0003_collected_by_edge.surql",
        "0004_dispatch.surql",
        "0005_flow_execution.surql",
        "0006_gate_decision.surql",
    }


def test_all_tables_and_edges_defined(db: Any) -> None:
    """Nomes de tabela/aresta idênticos à spec §2.3 (+ chunk/run via ADRs 0002/0008)."""
    defined = set(db.query("INFO FOR DB;")["tables"].keys())
    assert _TABLES <= defined
    assert _EDGES <= defined


def test_hnsw_index_on_chunk(db: Any) -> None:
    """Índice HNSW 768/cosseno sobre chunk.embedding (ADR-0006)."""
    idx = db.query("INFO FOR TABLE chunk;")["indexes"]["chunk_hnsw"]
    assert "HNSW" in idx
    assert "DIMENSION 768" in idx
    assert "COSINE" in idx


def test_chunk_rejects_wrong_dimension(db: Any) -> None:
    """Vetor de dimensão != 768 é rejeitado na borda pelo tipo array<float, 768>."""
    with pytest.raises(Exception, match="768"):
        db.query(
            "CREATE chunk:bad SET text='x', seq=0, embedding=$e, "
            "model='m', dim=3, task_type='SEMANTIC_SIMILARITY';",
            {"e": [0.1, 0.2, 0.3]},
        )


def test_flexible_fields_preserve_nested_payload(db: Any) -> None:
    """FLEXIBLE pegou: payload aninhado em run.error/item.metadata não é descartado."""
    db.query(
        "CREATE run:r SET worker='feed', status='error', error=$e, stats=$s;",
        {"e": {"kind": "http", "detail": {"status": 503}}, "s": {"fetched": 10}},
    )
    got = db.query("SELECT error, stats FROM run:r;")[0]
    assert got["error"]["detail"]["status"] == 503
    assert got["stats"]["fetched"] == 10


def test_relation_edge_rejects_missing_endpoint(db: Any) -> None:
    """ENFORCED: aresta para endpoint inexistente falha (proveniência não fica órfã)."""
    db.query("CREATE item:i SET external_id='e', content='c';")
    with pytest.raises(Exception):  # noqa: B017, PT011 (SurrealDB NotFoundError)
        db.query("RELATE distilled:ghost->derived_from->item:i;")


def test_relation_edge_allows_valid_endpoints(db: Any) -> None:
    """ENFORCED aceita a aresta quando os dois endpoints existem."""
    db.query("CREATE item:i SET external_id='e', content='c';")
    db.query("CREATE distilled:d SET summary='s';")
    db.query("RELATE distilled:d->derived_from->item:i;")
    linked = db.query("SELECT ->derived_from->item AS items FROM distilled:d;")[0]["items"]
    assert len(linked) == 1


def test_created_at_is_readonly_across_upsert(db: Any) -> None:
    """READONLY: re-UPSERT (SET) não reescreve created_at — idempotência do timestamp."""
    db.query("UPSERT source:s SET kind='rss', canonical='https://x/feed';")
    first = db.query("SELECT created_at FROM source:s;")[0]["created_at"]
    db.query("UPSERT source:s SET kind='rss', canonical='https://x/feed', title='changed';")
    second = db.query("SELECT created_at FROM source:s;")[0]["created_at"]
    assert first == second
