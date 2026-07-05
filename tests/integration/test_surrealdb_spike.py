"""Spike SurrealDB (M2) — prova empírica dos 5 comportamentos que sustentam a
aposta do banco único. Integração: exige SurrealDB via docker (-m integration).

Findings pinados aqui (servidor 3.1.5 + SDK 2.0.0, ver ADR-0005):
- KNN HNSW exige o parâmetro EF: `<|K,EF|>`; `<|K|>` sozinho FALHA ALTO (3.x).
- Transação via single query; CANCEL reverte silenciosamente (sem exceção).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import AsyncSurreal
from surrealdb.errors import SurrealError

from kubo.store import client

pytestmark = pytest.mark.integration


@pytest.fixture
def db() -> Iterator[Any]:
    cfg = replace(client.config(), database="test_spike")
    with client.connect(cfg) as conn:
        yield conn


# (a) documento -----------------------------------------------------------------
def test_insert_and_select_document(db: Any) -> None:
    db.query("REMOVE TABLE IF EXISTS person;")
    db.create("person:alice", {"name": "Alice", "age": 30})
    rows = db.select("person:alice")
    assert rows[0]["name"] == "Alice"
    assert rows[0]["age"] == 30


# (b) grafo ---------------------------------------------------------------------
def test_relate_and_graph_traversal(db: Any) -> None:
    db.query("REMOVE TABLE IF EXISTS knows; REMOVE TABLE IF EXISTS person;")
    db.query("CREATE person:a; CREATE person:b;")
    db.query("RELATE person:a->knows->person:b SET since = 2020;")
    rows = db.query("SELECT ->knows->person AS friends FROM person:a;")
    friends = rows[0]["friends"]
    assert len(friends) == 1
    assert friends[0].id == "b"


# (c) vetorial (HNSW) -----------------------------------------------------------
def test_hnsw_vector_search(db: Any) -> None:
    db.query("REMOVE TABLE IF EXISTS doc;")
    db.query("DEFINE INDEX doc_vec ON TABLE doc FIELDS embedding HNSW DIMENSION 4 DIST COSINE;")
    db.query("CREATE doc:1 SET embedding = [1.0, 0.0, 0.0, 0.0];")
    db.query("CREATE doc:2 SET embedding = [0.9, 0.1, 0.0, 0.0];")
    db.query("CREATE doc:3 SET embedding = [0.0, 0.0, 1.0, 0.0];")
    # EF obrigatório no operador KNN: <|K,EF|>
    res = db.query(
        "SELECT id, vector::distance::knn() AS dist FROM doc "
        "WHERE embedding <|2,40|> [1.0, 0.0, 0.0, 0.0] ORDER BY dist;"
    )
    assert [r["id"].id for r in res] == [1, 2]  # os 2 mais próximos, em ordem
    assert res[0]["dist"] == pytest.approx(0.0)  # vetor idêntico: distância ~0


def test_hnsw_knn_without_ef_raises(db: Any) -> None:
    """Contrato do 3.x: sem EF, o KNN FALHA ALTO (não retorna vazio silencioso).

    Em 2.x `<|K|>` sem EF devolvia [] — footgun: busca vazia indistinguível de
    'nada encontrado'. O 3.x rejeita o operador, forçando `<|K,EF|>`. Pinamos
    esse comportamento: é a razão de segurança operacional para o pin no 3.x.
    """
    db.query("REMOVE TABLE IF EXISTS doc;")
    db.query("DEFINE INDEX doc_vec ON TABLE doc FIELDS embedding HNSW DIMENSION 4 DIST COSINE;")
    db.query("CREATE doc:1 SET embedding = [1.0, 0.0, 0.0, 0.0];")
    with pytest.raises(SurrealError):
        db.query("SELECT id FROM doc WHERE embedding <|2|> [1.0, 0.0, 0.0, 0.0];")


# (d) transação -----------------------------------------------------------------
def test_transaction_commits_atomically(db: Any) -> None:
    db.query("REMOVE TABLE IF EXISTS acct;")
    db.query(
        "BEGIN; CREATE acct:1 SET bal = 100; CREATE acct:2 SET bal = 0; "
        "UPDATE acct:1 SET bal -= 50; UPDATE acct:2 SET bal += 50; COMMIT;"
    )
    rows = db.query("SELECT id, bal FROM acct ORDER BY id;")
    assert [r["bal"] for r in rows] == [50, 50]


def test_transaction_cancel_rolls_back(db: Any) -> None:
    """CANCEL reverte as escritas da transação (3.x: silencioso, sem exceção)."""
    db.query("REMOVE TABLE IF EXISTS acct;")
    db.query("CREATE acct:1 SET bal = 100;")
    db.query("BEGIN; UPDATE acct:1 SET bal = 555; CANCEL;")
    assert db.query("SELECT bal FROM acct:1;")[0]["bal"] == 100  # UPDATE revertido


# (e) async ---------------------------------------------------------------------
def test_async_query_and_traversal() -> None:
    cfg = replace(client.config(), database="test_spike_async")

    async def scenario() -> Any:
        db = AsyncSurreal(cfg.url)
        await db.signin({"username": cfg.user, "password": cfg.password})
        await db.use(cfg.namespace, cfg.database)
        await db.query("REMOVE TABLE IF EXISTS knows; REMOVE TABLE IF EXISTS person;")
        await db.query("CREATE person:a; CREATE person:b;")
        await db.query("RELATE person:a->knows->person:b;")
        rows = await db.query("SELECT ->knows->person AS friends FROM person:a;")
        await db.close()
        return rows

    rows = asyncio.run(scenario())
    assert len(rows[0]["friends"]) == 1
