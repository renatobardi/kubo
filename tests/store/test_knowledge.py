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
from surrealdb import RecordID

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


def _seed_distilled(db: Any, n: int) -> list[RecordID]:
    """Cria `n` destilados (cada um a partir de um item próprio) e devolve seus ids."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://feed")
    ids: list[RecordID] = []
    for i in range(n):
        item = knowledge.upsert_item(db, source=src, external_id=f"ext-{i}", content=f"c{i}")
        ids.append(knowledge.insert_distilled(db, item=item, summary=f"resumo {i}", chunks=[]))
    return ids


def test_list_distilled_paginates_without_overlap_or_gap(db: Any) -> None:
    """Duas páginas (limit=2) particionam o acervo: união = todos os ids, sem repetição
    nem buraco. É a garantia que o browse da UI precisa; não depende da resolução do
    created_at para o teste ser determinístico. (RecordID é unhashable no SDK 2.0.0 —
    conjuntos comparam a forma string do id.)"""
    all_ids = {str(rid) for rid in _seed_distilled(db, 5)}

    page1 = knowledge.list_distilled(db, limit=2, start=0)
    page2 = knowledge.list_distilled(db, limit=2, start=2)
    page3 = knowledge.list_distilled(db, limit=2, start=4)

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    seen = [str(d.id) for d in page1 + page2 + page3]
    assert len(seen) == len(set(seen))  # sem sobreposição entre páginas
    assert set(seen) == all_ids  # sem buraco
    # cada item carrega o summary (texto plano escapado na view)
    assert all(d.summary.startswith("resumo ") for d in page1)


def test_list_distilled_ordering_is_stable(db: Any) -> None:
    """A mesma consulta devolve a mesma ordem — paginação estável (ORDER BY determinístico)."""
    _seed_distilled(db, 4)
    first = [d.id for d in knowledge.list_distilled(db, limit=10, start=0)]
    second = [d.id for d in knowledge.list_distilled(db, limit=10, start=0)]
    assert first == second


def test_list_distilled_empty_acervo_returns_empty(db: Any) -> None:
    """Sem destilados, a lista é vazia (não None, não erro)."""
    assert knowledge.list_distilled(db, limit=10, start=0) == []


def test_list_distilled_clamps_hostile_bounds(db: Any) -> None:
    """limit/start vêm de query param (hostis na borda): limit<=0 vira >=1, start<0 vira 0.
    A store é a fronteira em que a spec confia — clamp aqui, não na view."""
    all_ids = {str(rid) for rid in _seed_distilled(db, 3)}
    # start negativo é tratado como 0 (primeira página)
    assert {str(d.id) for d in knowledge.list_distilled(db, limit=10, start=-5)} == all_ids
    # limit <= 0 ainda devolve ao menos 1 (não devolve a tabela inteira nem quebra)
    assert len(knowledge.list_distilled(db, limit=0, start=0)) == 1


def test_dashboard_counts_reflects_acervo(db: Any) -> None:
    """dashboard_counts conta destilados, itens e fontes do acervo (Painel)."""
    _seed_distilled(db, 3)  # cria 1 source + 3 items + 3 distilled
    counts = knowledge.dashboard_counts(db)
    assert counts.distilled == 3
    assert counts.items == 3
    assert counts.sources == 1


def test_dashboard_counts_empty_acervo_is_zero(db: Any) -> None:
    """Acervo vazio: todas as contagens são 0, não erro."""
    counts = knowledge.dashboard_counts(db)
    assert (counts.distilled, counts.items, counts.sources) == (0, 0, 0)


def test_recent_runs_newest_first_with_error_kind(db: Any) -> None:
    """recent_runs devolve as execuções mais recentes com o error.kind extraído:
    run ok -> error_kind None; run falha -> o kind estruturado (discriminação do Painel)."""
    ok = knowledge.start_run(db, worker="feed")
    knowledge.finish_run(db, ok)
    bad = knowledge.start_run(db, worker="distiller")
    knowledge.fail_run(db, bad, error={"kind": "rate_limit", "message": "quota"})

    runs = knowledge.recent_runs(db, limit=10)

    assert len(runs) == 2
    # newest first: `bad` (distiller) foi criado depois de `ok` (feed), com um
    # round-trip de finish_run entre eles — started_at estritamente maior.
    assert runs[0].worker == "distiller"
    assert runs[1].worker == "feed"
    # o run de falha carrega o error.kind estruturado; o ok não tem erro
    assert runs[0].status == "error"
    assert runs[0].error_kind == "rate_limit"
    assert runs[0].finished_at is not None
    assert runs[1].status == "ok"
    assert runs[1].error_kind is None


def test_recent_runs_respects_limit(db: Any) -> None:
    """limit trunca o resultado às N MAIS RECENTES (não N quaisquer)."""
    for i in range(4):
        knowledge.finish_run(db, knowledge.start_run(db, worker=f"w{i}"))  # w3 é o mais novo
    runs = knowledge.recent_runs(db, limit=2)
    assert [r.worker for r in runs] == ["w3", "w2"]


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


def test_attach_chunks_creates_chunks_and_makes_distilled_searchable(db: Any) -> None:
    """Caminho feliz (ADR-0013 §VI): dado um distilled criado com chunks=[],
    attach_chunks com 2 chunks cria os 2 registros `chunk` (com suas arestas
    chunk_of) e o distilled passa a ser devolvido por knowledge.search — prova de
    que os chunks estão de fato LIGADOS e buscáveis, não soltos no grafo."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    distilled_id = knowledge.insert_distilled(db, item=item_id, summary="resumo", chunks=[])

    knowledge.attach_chunks(
        db,
        distilled=distilled_id,
        chunks=[_chunk(0, _vec(1.0), text="primeiro"), _chunk(1, _vec(0.0, 1.0), text="segundo")],
    )

    assert _count(db, "chunk") == 2
    hits = knowledge.search(db, embedding=_vec(1.0), k=1)
    assert len(hits) == 1
    assert hits[0].distilled == distilled_id


def test_attach_chunks_preserves_existing_provenance(db: Any) -> None:
    """attach_chunks ANEXA, não deleta+recria (ADR-0013 §VI): o distilled continua
    sendo o MESMO record (mesmo id) e as arestas produced_by/mentions gravadas por
    um insert_distilled anterior (com run+entities) sobrevivem intactas depois."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    run_id = knowledge.start_run(db, worker="scribe")
    entity_id = knowledge.get_or_create_entity(db, name="Python")
    distilled_id = knowledge.insert_distilled(
        db, item=item_id, summary="resumo", chunks=[], run=run_id, entities=[entity_id]
    )

    knowledge.attach_chunks(db, distilled=distilled_id, chunks=[_chunk(0, _vec(1.0))])

    row = db.query(
        "SELECT ->produced_by->run AS r, ->mentions->entity AS e FROM $d;", {"d": distilled_id}
    )[0]
    assert row["r"] == [run_id]
    assert row["e"] == [entity_id]
    assert _count(db, "distilled") == 1  # não recriou


def test_attach_chunks_is_noop_when_distilled_already_has_chunks(db: Any) -> None:
    """Guarda de idempotência DENTRO de attach_chunks, não do chamador (ADR-0013 §VI):
    um distilled que JÁ tem chunk (aqui via insert_distilled com 1 chunk) ignora
    chunks diferentes numa nova chamada — a contagem não aumenta e nada novo é
    criado. É isso que torna o backfill retomável sem depender da disciplina de
    quem chama (script one-off pode re-rodar sem checar antes)."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    distilled_id = knowledge.insert_distilled(
        db, item=item_id, summary="resumo", chunks=[_chunk(0, _vec(1.0), text="original")]
    )

    knowledge.attach_chunks(
        db, distilled=distilled_id, chunks=[_chunk(1, _vec(0.0, 1.0), text="novo")]
    )

    assert _count(db, "chunk") == 1


def test_attach_chunks_with_empty_chunks_is_noop(db: Any) -> None:
    """attach_chunks(chunks=[]) não cria nada e não levanta — chamada seguramente
    inofensiva quando o backfill não tem nada a anexar para aquele distilled."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    distilled_id = knowledge.insert_distilled(db, item=item_id, summary="resumo", chunks=[])

    knowledge.attach_chunks(db, distilled=distilled_id, chunks=[])

    assert _count(db, "chunk") == 0


def test_attach_chunks_rejects_dim_provenance_mismatch_and_reverts(db: Any) -> None:
    """Mesma validação de borda de insert_distilled (helper extraído, ADR-0013 §VI):
    um Chunk cujo `dim` declarado (768) não bate com o tamanho real do embedding
    levanta ValueError, e a transação reverte — nenhum chunk fica gravado."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    distilled_id = knowledge.insert_distilled(db, item=item_id, summary="resumo", chunks=[])
    lying_chunk = Chunk(
        text="trecho",
        seq=0,
        embedding=_vec(1.0, dim=512),  # 512 posições reais
        model=_MODEL,
        dim=768,  # mas a proveniência mente: diz 768
        task_type=_TASK_TYPE,
    )

    with pytest.raises(ValueError, match="dim"):
        knowledge.attach_chunks(db, distilled=distilled_id, chunks=[lying_chunk])

    assert _count(db, "chunk") == 0


def test_distilled_without_chunks_returns_only_distilled_missing_chunks(db: Any) -> None:
    """distilled_without_chunks devolve (id, summary) de cada distilled SEM aresta
    chunk_of incoming — os candidatos ao backfill (ADR-0013 §VI/§VII). Um distilled
    já embeddado (resumo C, com 1 chunk) NÃO aparece na lista."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    item_b = knowledge.upsert_item(db, source=source_id, external_id="b", content="B")
    item_c = knowledge.upsert_item(db, source=source_id, external_id="c", content="C")
    knowledge.insert_distilled(db, item=item_a, summary="resumo A", chunks=[])
    knowledge.insert_distilled(db, item=item_b, summary="resumo B", chunks=[])
    knowledge.insert_distilled(db, item=item_c, summary="resumo C", chunks=[_chunk(0, _vec(1.0))])

    pending = knowledge.distilled_without_chunks(db)

    assert {summary for _, summary in pending} == {"resumo A", "resumo B"}
    for rid, summary in pending:
        assert isinstance(rid, RecordID)
        assert isinstance(summary, str)


def test_distilled_without_chunks_excludes_after_attach_chunks(db: Any) -> None:
    """Depois de attach_chunks anexar um chunk a um dos pendentes, ele SAI da
    lista — prova de retomabilidade: o backfill script pode re-rodar e processar
    só o restante, sem reprocessar quem já foi resolvido."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    item_b = knowledge.upsert_item(db, source=source_id, external_id="b", content="B")
    distilled_a = knowledge.insert_distilled(db, item=item_a, summary="resumo A", chunks=[])
    knowledge.insert_distilled(db, item=item_b, summary="resumo B", chunks=[])

    knowledge.attach_chunks(db, distilled=distilled_a, chunks=[_chunk(0, _vec(1.0))])

    pending = knowledge.distilled_without_chunks(db)
    assert {summary for _, summary in pending} == {"resumo B"}


def test_distilled_without_chunks_returns_empty_when_no_distilled(db: Any) -> None:
    """Banco sem nenhum distilled: distilled_without_chunks devolve []."""
    assert knowledge.distilled_without_chunks(db) == []


def test_items_without_distilled_filters_and_limits(db: Any) -> None:
    """items_without_distilled devolve (id, title, content) de cada item SEM
    nenhum derived_from incoming — candidatos à destilação nova (ADR-0013
    §III.1/§III.7). Um item já destilado (C) NÃO aparece na lista."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = knowledge.upsert_item(
        db, source=source_id, external_id="a", content="conteúdo A", title="Título A"
    )
    item_b = knowledge.upsert_item(
        db, source=source_id, external_id="b", content="conteúdo B", title="Título B"
    )
    item_c = knowledge.upsert_item(db, source=source_id, external_id="c", content="conteúdo C")
    knowledge.insert_distilled(db, item=item_c, summary="tem destilado", chunks=[])

    pending = knowledge.items_without_distilled(db, limit=10)

    assert len(pending) == 2
    assert {str(rid) for rid, _, _ in pending} == {str(item_a), str(item_b)}
    by_id = {str(rid): (title, content) for rid, title, content in pending}
    assert by_id[str(item_a)] == ("Título A", "conteúdo A")
    assert by_id[str(item_b)] == ("Título B", "conteúdo B")


def test_items_without_distilled_respects_limit(db: Any) -> None:
    """Com 3 items pendentes e limit=2, devolve exatamente 2 — o worker consome
    em lotes, não a lista inteira de uma vez."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    knowledge.upsert_item(db, source=source_id, external_id="b", content="B")
    knowledge.upsert_item(db, source=source_id, external_id="c", content="C")

    pending = knowledge.items_without_distilled(db, limit=2)

    assert len(pending) == 2


def test_items_without_distilled_is_deterministically_ordered(db: Any) -> None:
    """Duas chamadas seguidas devolvem a MESMA ordem (por id) — sem isso, um
    worker que processa em lotes poderia pular ou reprocessar itens entre lotes."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    knowledge.upsert_item(db, source=source_id, external_id="b", content="B")
    knowledge.upsert_item(db, source=source_id, external_id="c", content="C")

    first = knowledge.items_without_distilled(db, limit=10)
    second = knowledge.items_without_distilled(db, limit=10)

    assert first == second


def test_items_without_distilled_returns_empty_when_none_pending(db: Any) -> None:
    """Banco sem item pendente (nenhum item, ou todo item já destilado) devolve []."""
    assert knowledge.items_without_distilled(db, limit=10) == []

    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    knowledge.insert_distilled(db, item=item_id, summary="resumo", chunks=[])

    assert knowledge.items_without_distilled(db, limit=10) == []


def test_items_without_distilled_preserves_none_title_and_exact_content(db: Any) -> None:
    """Um item criado sem title devolve title is None e o content exato — nenhuma
    normalização silenciosa do conteúdo bruto coletado."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="a", content="conteúdo exato sem título"
    )

    pending = knowledge.items_without_distilled(db, limit=10)

    assert len(pending) == 1
    rid, title, content = pending[0]
    assert rid == item_id
    assert title is None
    assert content == "conteúdo exato sem título"


def test_items_without_distilled_excludes_empty_and_whitespace_content(db: Any) -> None:
    """Item com content vazio ("") ou só-whitespace NUNCA é candidato à destilação
    (ADR-0013 §III: precondição do run vivo) — mandar o vazio ao LLM alucinaria um
    summary que persistiria com proveniência, item saindo do funil para sempre. Só o
    item com conteúdo real (C) aparece; os link-posts sem corpo (A vazio, B whitespace)
    ficam de fora."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    knowledge.upsert_item(db, source=source_id, external_id="a", content="")
    knowledge.upsert_item(db, source=source_id, external_id="b", content="   \n\t  ")
    item_c = knowledge.upsert_item(db, source=source_id, external_id="c", content="conteúdo real")

    pending = knowledge.items_without_distilled(db, limit=10)

    assert len(pending) == 1
    rid, _, content = pending[0]
    assert rid == item_c
    assert content == "conteúdo real"


def test_insert_distilled_mentions_are_atomic_no_orphan_on_late_failure(db: Any) -> None:
    """GUARDA de atomicidade (ADR-0013 §III.8): se o RELATE mentions (statement
    TARDIO, após CREATE distilled e CREATE chunk) falha porque a entity referenciada
    não existe (mentions é ENFORCED — a aresta rejeita endpoint inexistente), a
    transação INTEIRA reverte. Nada sobra: nem o distilled, nem os chunks criados
    antes do RELATE ruim. Este teste é um PIN contra refactor: hoje já passa verde
    porque insert_distilled já escreve tudo numa única transação; se um futuro
    refactor separar mentions numa chamada pós-commit, este teste vira RED."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(
        db, source=source_id, external_id="ep-1", content="conteúdo bruto"
    )
    run_id = knowledge.start_run(db, worker="scribe")
    missing_entity = RecordID("entity", "nao-existe")

    with pytest.raises(StoreError):
        knowledge.insert_distilled(
            db,
            item=item_id,
            summary="não deveria persistir",
            chunks=[_chunk(0, _vec(1.0))],
            run=run_id,
            entities=[missing_entity],
        )

    assert _count(db, "distilled") == 0
    assert _count(db, "chunk") == 0


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


def test_read_distilled_returns_full_provenance_view(db: Any) -> None:
    """Caminho feliz (ADR-0013 §8.5): read_distilled resolve distilled -> item ->
    source E distilled -> run numa leitura só, com os campos que o CLI mostra
    (`kubo query` usa `.summary`, `kubo show --provenance` usa o resto) —
    substitui a antiga `provenance` (só ids de source, insuficiente para exibir)."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed", title="Feed X")
    item_id = knowledge.upsert_item(
        db,
        source=source_id,
        external_id="ep-1",
        content="bruto",
        url="https://x/ep-1",
        title="Episódio 1",
    )
    run_id = knowledge.start_run(db, worker="scribe")
    distilled_id = knowledge.insert_distilled(
        db,
        item=item_id,
        summary="resumo destilado",
        chunks=[],
        claims=["afirmação A", "afirmação B"],
        run=run_id,
    )

    view = knowledge.read_distilled(db, distilled_id)

    assert view is not None
    assert view.id == distilled_id
    assert view.summary == "resumo destilado"
    assert view.claims == ["afirmação A", "afirmação B"]

    assert len(view.items) == 1
    item_view = view.items[0]
    assert isinstance(item_view, knowledge.ProvenanceItem)
    assert item_view.external_id == "ep-1"
    assert item_view.url == "https://x/ep-1"
    assert item_view.title == "Episódio 1"
    assert item_view.source_canonical == "https://x/feed"
    assert item_view.source_title == "Feed X"
    assert item_view.source_kind == "rss"

    assert len(view.runs) == 1
    assert view.runs[0] == knowledge.RunRef(worker="scribe", status="running")


def test_read_distilled_without_run_has_empty_runs_but_keeps_items(db: Any) -> None:
    """Um distilled criado SEM run devolve `runs == []` (produced_by não é
    obrigatório), mas `items` continua com 1 entrada — derived_from é ENFORCED
    pelo schema, então item->source sempre resolve. claims default a []."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    distilled_id = knowledge.insert_distilled(db, item=item_id, summary="resumo sem run", chunks=[])

    view = knowledge.read_distilled(db, distilled_id)

    assert view is not None
    assert view.summary == "resumo sem run"
    assert view.claims == []
    assert len(view.items) == 1
    assert view.runs == []


def test_read_distilled_returns_none_for_nonexistent_id(db: Any) -> None:
    """read_distilled de um id que não existe no grafo devolve None — não levanta
    e não confunde 'sem proveniência' com 'destilado inexistente'."""
    assert knowledge.read_distilled(db, RecordID("distilled", "nao-existe")) is None


def test_read_distilled_handles_missing_optional_fields(db: Any) -> None:
    """Robustez a campos opcionais ausentes: source sem `title` devolve
    `source_title is None`; item sem `url`/`title` devolve esses campos None
    também — nenhum KeyError por causa de um dado opcional não preenchido."""
    source_id = knowledge.upsert_source(db, kind="youtube", canonical="https://y/channel")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="v-1", content="bruto")
    distilled_id = knowledge.insert_distilled(db, item=item_id, summary="resumo", chunks=[])

    view = knowledge.read_distilled(db, distilled_id)

    assert view is not None
    item_view = view.items[0]
    assert item_view.url is None
    assert item_view.title is None
    assert item_view.source_title is None
    assert item_view.source_kind == "youtube"


# ---------------------------------------------------------------------------
# M1 (sessão 0010): leituras da UI de Conhecimento + Execuções.
# Projeção 1-nível provada pelo probe (title 1-hop, source 2-hop encadeado);
# time::max para datetime (math::max explode); travessia volta array (unwrap).
# ---------------------------------------------------------------------------


def test_list_distilled_card_carries_title_source_and_date(db: Any) -> None:
    """O card do browse (E3) leva o título do item (via derived_from), a fonte
    (canonical + kind a 2 hops) e a data — não só id+summary."""
    src = knowledge.upsert_source(db, kind="youtube", canonical="https://y/@canal", title="Canal")
    item = knowledge.upsert_item(
        db, source=src, external_id="v1", content="c", title="Título do Item"
    )
    knowledge.insert_distilled(db, item=item, summary="resumo curto", chunks=[])

    cards = knowledge.list_distilled(db, limit=20, start=0)

    assert len(cards) == 1
    card = cards[0]
    assert card.title == "Título do Item"
    assert card.source_canonical == "https://y/@canal"
    assert card.source_kind == "youtube"
    assert card.summary == "resumo curto"
    assert card.created_at  # carimbo presente (string)


def test_list_distilled_title_falls_back_to_summary_first_line(db: Any) -> None:
    """Item sem title (NULL): o card usa a 1ª linha não-vazia do summary como título
    (E3) — nunca fica sem rótulo. Travessia sem título volta [None], vira None, cai no fallback."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item = knowledge.upsert_item(db, source=src, external_id="e1", content="c")  # sem title
    knowledge.insert_distilled(
        db, item=item, summary="Primeira linha do resumo.\nSegunda.", chunks=[]
    )

    card = knowledge.list_distilled(db, limit=20, start=0)[0]

    assert card.title == "Primeira linha do resumo."


def test_list_entities_counts_mentions_ordered_desc(db: Any) -> None:
    """list_entities conta menções por entidade (array::len(<-mentions)) e ordena
    do mais mencionado ao menos (E2) — sem sparkline, sem relações."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item = knowledge.upsert_item(db, source=src, external_id="e1", content="c")
    popular = knowledge.get_or_create_entity(db, name="Python", kind="tecnologia")
    rare = knowledge.get_or_create_entity(db, name="Rust", kind="tecnologia")
    knowledge.insert_distilled(db, item=item, summary="d1", chunks=[], entities=[popular, rare])
    knowledge.insert_distilled(db, item=item, summary="d2", chunks=[], entities=[popular])

    entities = knowledge.list_entities(db, limit=20, start=0)

    assert [e.name for e in entities] == ["Python", "Rust"]
    assert entities[0].mentions == 2
    assert entities[0].kind == "tecnologia"
    assert entities[1].mentions == 1


def test_read_entity_returns_mentioning_distilled_cards(db: Any) -> None:
    """read_entity devolve a entidade + os destilados que a mencionam como cards
    (título/fonte/data, mesma resolução do browse). None quando o id não existe."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed", title="Feed")
    item = knowledge.upsert_item(db, source=src, external_id="e1", content="c", title="Post A")
    ent = knowledge.get_or_create_entity(db, name="Python", kind="tecnologia")
    knowledge.insert_distilled(db, item=item, summary="fala de python", chunks=[], entities=[ent])

    view = knowledge.read_entity(db, ent)

    assert view is not None
    assert view.name == "Python"
    assert view.kind == "tecnologia"
    assert view.mentions == 1
    assert len(view.distilled) == 1
    assert view.distilled[0].title == "Post A"
    assert view.distilled[0].source_canonical == "https://x/feed"

    assert knowledge.read_entity(db, RecordID("entity", "nao-existe")) is None


def test_sources_with_stats_counts_items_and_last_collection(db: Any) -> None:
    """sources_with_stats: por fonte, quantos itens acumulados e o carimbo da última
    coleta (time::max, E4). Fonte sem item nenhum → last_collected_at None (badge trata)."""
    active = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed", title="Ativa")
    knowledge.upsert_item(db, source=active, external_id="e1", content="c")
    knowledge.upsert_item(db, source=active, external_id="e2", content="c")
    knowledge.upsert_source(db, kind="site", canonical="https://empty/site")

    stats = {s.canonical: s for s in knowledge.sources_with_stats(db)}

    assert stats["https://x/feed"].items == 2
    assert stats["https://x/feed"].kind == "rss"
    assert stats["https://x/feed"].title == "Ativa"
    assert stats["https://x/feed"].last_collected_at is not None
    assert stats["https://empty/site"].items == 0
    assert stats["https://empty/site"].last_collected_at is None


def test_list_runs_paginated_with_error_and_derived_items(db: Any) -> None:
    """list_runs pagina as execuções (mais recentes primeiro) com o erro estruturado
    completo (kind + objeto p/ o painel expansível) e o nº de itens derivado de stats
    (E6: `items` do feed, `distilled` do distiller; senão None)."""
    feed = knowledge.start_run(db, worker="feed")
    knowledge.finish_run(db, feed, stats={"entries_seen": 12, "items": 5})
    distiller = knowledge.start_run(db, worker="distiller")
    knowledge.fail_run(db, distiller, error={"kind": "rate_limit", "message": "quota estourada"})

    runs = knowledge.list_runs(db, limit=20, start=0)

    assert [r.worker for r in runs] == ["distiller", "feed"]  # newest first
    failed, ok = runs
    assert failed.status == "error"
    assert failed.error_kind == "rate_limit"
    assert failed.error == {"kind": "rate_limit", "message": "quota estourada"}
    assert failed.items is None  # distiller sem distilled>0 neste run
    assert ok.status == "ok"
    assert ok.error is None
    assert ok.items == 5  # derivado de stats["items"]


def test_list_runs_derives_items_from_distiller_stats(db: Any) -> None:
    """Quando o worker é o distiller, o nº de itens vem de stats['distilled'] (E6)."""
    run = knowledge.start_run(db, worker="distiller")
    knowledge.finish_run(db, run, stats={"distilled": 7, "malformed": 1})

    assert knowledge.list_runs(db, limit=20, start=0)[0].items == 7


def test_list_runs_pagination_start_skips(db: Any) -> None:
    """start pula as N execuções mais recentes — paginação estável."""
    for i in range(3):
        knowledge.finish_run(db, knowledge.start_run(db, worker=f"w{i}"))
    page2 = knowledge.list_runs(db, limit=2, start=2)
    assert [r.worker for r in page2] == ["w0"]
