"""Contrato de comportamento da store de conhecimento (integração, SurrealDB).

Cobre plano 0003 §3.2.1: upsert idempotente de source/item, dedup de entity por
normalização, escrita atômica de destilado (feliz e com rollback), proveniência
distilled->item->source e busca vetorial que devolve o destilado, não o chunk
órfão. `kubo.store.knowledge` ainda é STUB (NotImplementedError) — estes testes
devem falhar por isso agora; ficam verdes quando a implementação (GREEN) entrar.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.errors import StoreError
from kubo.store import client, knowledge, migrations
from kubo.store.knowledge import Chunk

pytestmark = pytest.mark.integration

_KNOWLEDGE_DB = "test_knowledge"
_DIM = 768
_MODEL = "gemini-embedding-001"
_TASK_TYPE = "SEMANTIC_SIMILARITY"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_KNOWLEDGE_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_KNOWLEDGE_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_KNOWLEDGE_DB};")


def _vec(*nonzero: float, dim: int = _DIM) -> list[float]:
    """Vetor de `dim` posições: os valores de `nonzero` nas primeiras posições, resto zero.

    Usado para montar embeddings de teste sem hardcodar 768 números — e para
    montar pares ortogonais (`_vec(1.0)` vs `_vec(0.0, 1.0)`) de forma legível.
    """
    values = [float(v) for v in nonzero] + [0.0] * (dim - len(nonzero))
    return values[:dim]


def _count(db: Any, table: str) -> int:
    """Contagem de registros na tabela — nome de tabela é sempre um literal interno do teste."""
    rows: list[dict[str, Any]] = db.query(f"SELECT count() FROM {table} GROUP ALL;")  # noqa: S608
    return int(rows[0]["count"]) if rows else 0


def _chunk(seq: int, embedding: list[float], text: str = "trecho") -> Chunk:
    """Chunk válido (768 dims) com a tripla de proveniência do ADR-0006."""
    return Chunk(
        text=text,
        seq=seq,
        embedding=embedding,
        model=_MODEL,
        dim=_DIM,
        task_type=_TASK_TYPE,
    )


def test_upsert_source_is_idempotent(db: Any) -> None:
    """2x upsert_source com o mesmo canonical resolve ao MESMO record, sem duplicar
    e sem reescrever created_at (READONLY) — chave natural, não SELECT-then-CREATE."""
    first_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    first_created = db.query("SELECT created_at FROM $s;", {"s": first_id})[0]["created_at"]

    second_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    second_created = db.query("SELECT created_at FROM $s;", {"s": second_id})[0]["created_at"]

    assert first_id == second_id
    assert first_created == second_created
    assert _count(db, "source") == 1


def test_upsert_item_is_idempotent_and_creates_from_source_edge(db: Any) -> None:
    """2x upsert_item para o mesmo (source, external_id) resolve ao MESMO record e
    cria a aresta item -[from_source]-> source uma única vez."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")

    first_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="conteúdo bruto"
    )
    second_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="conteúdo bruto"
    )

    assert first_id == second_id
    assert _count(db, "item") == 1

    linked = db.query("SELECT ->from_source->source AS srcs FROM $i;", {"i": first_id})[0]["srcs"]
    assert linked == [source_id]


def test_upsert_item_with_run_creates_collected_by_edge(db: Any) -> None:
    """upsert_item(run=...) cria a aresta item -[collected_by]-> run — proveniência
    de execução (quem coletou), simétrica a produced_by (ADR-0008 emenda 0005)."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    run_id = knowledge.start_run(db, worker="feed")

    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="bruto", run=run_id
    )

    runs = db.query("SELECT ->collected_by->run AS runs FROM $i;", {"i": item_id})[0]["runs"]
    assert runs == [run_id]


def test_upsert_item_re_collection_is_last_wins(db: Any) -> None:
    """Re-coleta por outra run reescreve a aresta (DELETE+RELATE na mesma transação):
    collected_by aponta para a ÚLTIMA run coletora, nunca acumula (last-wins). O
    histórico completo vive na tabela run, não na aresta."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    run_a = knowledge.start_run(db, worker="feed")
    run_b = knowledge.start_run(db, worker="feed")

    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="bruto", run=run_a
    )
    knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto", run=run_b)

    runs = db.query("SELECT ->collected_by->run AS runs FROM $i;", {"i": item_id})[0]["runs"]
    assert runs == [run_b]
    assert _count(db, "collected_by") == 1


def test_upsert_item_without_run_preserves_existing_collected_by(db: Any) -> None:
    """upsert_item sem run NÃO toca collected_by — não cria aresta e, crucialmente,
    não apaga proveniência já registrada por uma coleta anterior. Um upsert sem run
    não pode destruir a proveniência de quem coletou (advisor)."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    run_a = knowledge.start_run(db, worker="feed")

    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="bruto", run=run_a
    )
    # Re-upsert SEM run (ex.: outro produtor sem contexto de execução).
    knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")

    runs = db.query("SELECT ->collected_by->run AS runs FROM $i;", {"i": item_id})[0]["runs"]
    assert runs == [run_a]  # preservada


def test_upsert_item_without_run_creates_no_collected_by_edge(db: Any) -> None:
    """Um item nunca-coletado-por-run (sem param run) não tem aresta collected_by."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")

    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")

    runs = db.query("SELECT ->collected_by->run AS runs FROM $i;", {"i": item_id})[0]["runs"]
    assert runs == []


def test_get_or_create_entity_dedups_by_normalized_name(db: Any) -> None:
    """ "Python" e "  python " resolvem à MESMA entity — dedup por `normalize_entity`,
    não por igualdade literal da string de entrada."""
    first_id = knowledge.get_or_create_entity(db, name="Python")
    second_id = knowledge.get_or_create_entity(db, name="  python ")

    assert first_id == second_id
    assert _count(db, "entity") == 1


def test_insert_distilled_creates_record_and_all_edges_atomically(db: Any) -> None:
    """Caminho feliz: um insert_distilled cria o `distilled`, `derived_from -> item`,
    os `chunk` + `chunk_of -> distilled`, `produced_by -> run` e `mentions -> entity`
    numa única escrita — nada fica de fora nem precisa de segunda chamada."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="conteúdo bruto"
    )
    run_id = knowledge.start_run(db, worker="scribe")
    entity_id = knowledge.get_or_create_entity(db, name="Python")
    chunks = [
        _chunk(0, _vec(1.0), text="primeiro trecho"),
        _chunk(1, _vec(0.0, 1.0), text="segundo trecho"),
    ]

    distilled_id = knowledge.insert_distilled(
        db,
        item=item_id,
        summary="resumo do episódio",
        chunks=chunks,
        run=run_id,
        entities=[entity_id],
    )

    assert _count(db, "distilled") == 1
    assert _count(db, "chunk") == 2

    derived = db.query("SELECT ->derived_from->item AS items FROM $d;", {"d": distilled_id})[0][
        "items"
    ]
    assert derived == [item_id]

    produced = db.query("SELECT ->produced_by->run AS runs FROM $d;", {"d": distilled_id})[0][
        "runs"
    ]
    assert produced == [run_id]

    mentioned = db.query("SELECT ->mentions->entity AS entities FROM $d;", {"d": distilled_id})[0][
        "entities"
    ]
    assert mentioned == [entity_id]

    chunk_of = db.query("SELECT <-chunk_of<-chunk AS chunks FROM $d;", {"d": distilled_id})[0][
        "chunks"
    ]
    assert len(chunk_of) == 2


def test_insert_distilled_rejects_wrong_dimension_and_reverts_everything(db: Any) -> None:
    """Um chunk com embedding de dimensão != 768 levanta StoreError E não deixa
    NENHUM rastro — nem o `distilled`, nem os `chunk` já processados antes do
    ruim na lista. Prova o wrapper transacional (ADR-0005): tudo ou nada."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="conteúdo bruto"
    )
    bad_chunk = Chunk(
        text="trecho ruim",
        seq=0,
        embedding=[0.1, 0.2, 0.3],
        model=_MODEL,
        dim=3,
        task_type=_TASK_TYPE,
    )

    with pytest.raises(StoreError):
        knowledge.insert_distilled(
            db,
            item=item_id,
            summary="resumo que não deveria persistir",
            chunks=[bad_chunk],
        )

    assert _count(db, "distilled") == 0
    assert _count(db, "chunk") == 0


def test_insert_distilled_rejects_dim_provenance_mismatch(db: Any) -> None:
    """Um chunk cujo `dim` (proveniência) não bate com len(embedding) levanta
    ValueError na borda ANTES de tocar o banco — o schema garante len == 768, mas
    não que o `dim` registrado seja verdadeiro; um `dim` mentiroso corromperia a
    proveniência do re-embed. Nada é persistido."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    lying_chunk = Chunk(
        text="trecho",
        seq=0,
        embedding=_vec(1.0),  # 768 posições, válido para o schema
        model=_MODEL,
        dim=512,  # mas a proveniência mente: diz 512
        task_type=_TASK_TYPE,
    )

    with pytest.raises(ValueError, match="dim"):
        knowledge.insert_distilled(db, item=item_id, summary="resumo", chunks=[lying_chunk])

    assert _count(db, "distilled") == 0
    assert _count(db, "chunk") == 0


def test_provenance_traces_distilled_to_source(db: Any) -> None:
    """provenance(distilled) devolve uma lista que inclui a source original —
    o embrião da prova dos 90 dias (distilled -> item -> source)."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="conteúdo bruto"
    )
    distilled_id = knowledge.insert_distilled(
        db,
        item=item_id,
        summary="resumo",
        chunks=[_chunk(0, _vec(1.0))],
    )

    trail = knowledge.provenance(db, distilled_id)

    assert source_id in trail


def test_list_sources_returns_all_with_their_fields(db: Any) -> None:
    """list_sources devolve toda source com id/canonical/kind/title — leitura única
    que o import usa para resolver a source de um item por canonical (sem regravá-la,
    ADR-0012) e a UI da fase 1 para listar; substitui queries de source espalhadas."""
    a = knowledge.upsert_source(db, kind="rss", canonical="https://a/feed", title="A")
    knowledge.upsert_source(db, kind="youtube", canonical="https://b")

    by_canonical = {s.canonical: s for s in knowledge.list_sources(db)}

    assert set(by_canonical) == {"https://a/feed", "https://b"}
    assert by_canonical["https://a/feed"].id == a
    assert by_canonical["https://a/feed"].kind == "rss"
    assert by_canonical["https://a/feed"].title == "A"
    assert by_canonical["https://b"].title is None


def test_item_index_maps_external_id_to_item(db: Any) -> None:
    """item_index devolve o mapa external_id -> item de todos os itens numa leitura —
    o import resolve derived_from (distilled -> item pela chave natural) e detecta
    itens já presentes por aqui, sem 1 query por linha nem SELECT de item espalhado."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    i1 = knowledge.upsert_item(db, source=src, external_id="ext-1", content="a")
    i2 = knowledge.upsert_item(db, source=src, external_id="ext-2", content="b")

    assert knowledge.item_index(db) == {"ext-1": i1, "ext-2": i2}


def test_item_index_collapses_external_id_collision_to_one_entry(db: Any) -> None:
    """external_id colidindo entre duas sources: item_index mantém UMA ocorrência
    (1ª por id) e loga — não vincula silenciosamente ao item errado (achado do
    CodeRabbit); external_id é chave natural, colisão não é esperada."""
    s1 = knowledge.upsert_source(db, kind="rss", canonical="https://a/feed")
    s2 = knowledge.upsert_source(db, kind="rss", canonical="https://b/feed")
    i1 = knowledge.upsert_item(db, source=s1, external_id="dup", content="x")
    i2 = knowledge.upsert_item(db, source=s2, external_id="dup", content="y")

    index = knowledge.item_index(db)

    assert set(index) == {"dup"}  # duas linhas de origem, uma entrada
    assert index["dup"] in (i1, i2)


def test_distilled_for_returns_empty_when_item_has_no_distilled(db: Any) -> None:
    """distilled_for(item) devolve [] quando nenhum destilado deriva do item —
    a leitura que o import one-off usa para pular itens já destilados (insert_distilled
    NÃO é idempotente; sem esta checagem, re-rodar o corpus duplicaria os destilados)."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")

    assert knowledge.distilled_for(db, item_id) == []


def test_distilled_for_returns_distilled_derived_from_the_item(db: Any) -> None:
    """distilled_for(item) devolve os destilados que derivam do item (item <-derived_from<-
    distilled) e SÓ eles — o destilado de outro item não vaza. É a prova de no-op do
    corpus de distillations: item com destilado é pulado na re-execução."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    item_b = knowledge.upsert_item(db, source=source_id, external_id="b", content="B")

    distilled_a = knowledge.insert_distilled(db, item=item_a, summary="resumo A", chunks=[])
    knowledge.insert_distilled(db, item=item_b, summary="resumo B", chunks=[])

    assert knowledge.distilled_for(db, item_a) == [distilled_a]


def test_search_returns_the_distilled_for_the_nearest_chunk(db: Any) -> None:
    """search(embedding=vetorA, k=1) devolve o SearchHit do destilado A (não B) quando
    vetorA é ortogonal a vetorB — e o hit expõe o `distilled` (o conhecimento),
    não só o `chunk` órfão."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    item_b = knowledge.upsert_item(db, source=source_id, external_id="b", content="B")

    distilled_a = knowledge.insert_distilled(
        db, item=item_a, summary="resumo A", chunks=[_chunk(0, _vec(1.0), text="A")]
    )
    knowledge.insert_distilled(
        db, item=item_b, summary="resumo B", chunks=[_chunk(0, _vec(0.0, 1.0), text="B")]
    )

    hits = knowledge.search(db, embedding=_vec(1.0), k=1)

    assert len(hits) == 1
    assert hits[0].distilled == distilled_a


def test_run_lifecycle_running_then_ok_or_error(db: Any) -> None:
    """start_run abre em 'running'; finish_run fecha em 'ok' com finished_at
    preenchido; fail_run (em outro run) fecha em 'error' preservando o erro
    estruturado aninhado, também com finished_at preenchido."""
    run_id = knowledge.start_run(db, worker="feed")
    started = db.query("SELECT status, finished_at FROM $r;", {"r": run_id})[0]
    assert started["status"] == "running"
    assert started["finished_at"] is None

    knowledge.finish_run(db, run_id)
    finished = db.query("SELECT status, finished_at FROM $r;", {"r": run_id})[0]
    assert finished["status"] == "ok"
    assert finished["finished_at"] is not None


def test_finish_run_persists_stats(db: Any) -> None:
    """finish_run(stats=...) grava os contadores no campo FLEXIBLE run.stats
    (carry-over do 0003: o worker fake com métricas é o primeiro consumidor)."""
    run_id = knowledge.start_run(db, worker="feed")

    knowledge.finish_run(db, run_id, stats={"items_seen": 10, "items_written": 3})

    row = db.query("SELECT status, stats FROM $r;", {"r": run_id})[0]
    assert row["status"] == "ok"
    assert row["stats"]["items_seen"] == 10
    assert row["stats"]["items_written"] == 3

    other_run_id = knowledge.start_run(db, worker="feed")
    knowledge.fail_run(db, other_run_id, error={"kind": "http", "detail": {"status": 503}})
    failed = db.query("SELECT status, error, finished_at FROM $r;", {"r": other_run_id})[0]
    assert failed["status"] == "error"
    assert failed["error"]["detail"]["status"] == 503
    assert failed["finished_at"] is not None
