"""Contrato de comportamento da store de conhecimento (integração, SurrealDB).

Cobre plano 0003 §3.2.1: upsert idempotente de source/item, dedup de entity por
normalização, escrita atômica de destilado (feliz e com rollback), proveniência
distilled->item->source e busca vetorial que devolve o destilado, não o chunk
órfão.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import (
    DuplicateSourceError,
    SourceHasHistoryError,
    StaleSourceError,
    StoreError,
)
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


def test_create_source_mints_surrogate_id_decoupled_from_canonical(db: Any) -> None:
    """create_source cunha um id SURROGATE (não derivado da canonical) e o Cadastro nasce
    ativo: enabled=true, tags=[], sem archived_at. É a escrita da UI (ADR-0025) — cadastro
    novo com id desacoplado da URL, distinto de upsert_source (chave natural sha256)."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed", title="Feed X")

    assert rid != knowledge._rid("source", "https://x/feed")  # id NÃO deriva da canonical
    row = db.query("SELECT canonical, title, enabled, archived_at, tags FROM $s;", {"s": rid})[0]
    assert row["enabled"] is True
    assert row["tags"] == []
    assert row.get("archived_at") is None
    assert row["title"] == "Feed X"
    assert _count(db, "source") == 1


def test_create_source_rejects_duplicate_kind_canonical(db: Any) -> None:
    """Duplicata (mesmo kind + mesma canonical) é ERRO da store, não no-op: a segunda
    chamada levanta DuplicateSourceError e nenhum segundo record nasce (constraint da
    store, não checagem na view — ticket #105)."""
    knowledge.create_source(db, kind="rss", canonical="https://x/feed")

    with pytest.raises(DuplicateSourceError):
        knowledge.create_source(db, kind="rss", canonical="https://x/feed")

    assert _count(db, "source") == 1


def test_create_source_allows_same_canonical_across_kinds(db: Any) -> None:
    """A unicidade é composta (kind, canonical): a MESMA canonical em kinds diferentes são
    dois Cadastros distintos — exercita o índice UNIQUE(kind, canonical) da 0009, que a
    global UNIQUE(canonical) anterior barraria."""
    a = knowledge.create_source(db, kind="rss", canonical="https://x")
    b = knowledge.create_source(db, kind="github-repo", canonical="https://x")

    assert a != b
    assert _count(db, "source") == 2


def test_upsert_source_reuses_record_created_by_ui(db: Any) -> None:
    """Lookup-first (a mina que #105 desarma): um Cadastro criado pela UI (id surrogate) é
    REUSADO pelo coletor. upsert_source resolve à mesma (kind, canonical) sem cunhar um
    segundo record que o índice UNIQUE(kind, canonical) barraria — quebrando a coleta."""
    ui_id = knowledge.create_source(db, kind="github-repo", canonical="https://github.com/o/r")

    collected = knowledge.upsert_source(db, kind="github-repo", canonical="https://github.com/o/r")

    assert collected == ui_id
    assert _count(db, "source") == 1


def test_upsert_source_reuses_legacy_sha256_record(db: Any) -> None:
    """Lookup-first preserva ids legados: um record com id sha256(canonical) (esquema
    anterior à 0025) é resolvido pela CHAVE NATURAL, não substituído por um id novo — ids
    existentes e suas arestas ficam intactos (ADR-0025)."""
    legacy = knowledge._rid("source", "https://legacy/feed")
    db.query("CREATE $r SET kind = 'rss', canonical = 'https://legacy/feed';", {"r": legacy})

    resolved = knowledge.upsert_source(db, kind="rss", canonical="https://legacy/feed")

    assert resolved == legacy
    assert _count(db, "source") == 1


def test_get_source_returns_full_cadastro(db: Any) -> None:
    """get_source expõe o Cadastro INTEIRO por id (kind, canonical, title, tags, enabled,
    archived_at) — a leitura que o form de edição (#106) precisa e que list_sources/
    sources_with_stats não dão (não trazem tags nem enabled)."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed", title="Feed X")

    got = knowledge.get_source(db, rid)

    assert got is not None
    assert got.id == rid
    assert got.kind == "rss"
    assert got.canonical == "https://x/feed"
    assert got.title == "Feed X"
    assert got.tags == []
    assert got.enabled is True
    assert got.archived_at is None


def test_get_source_absent_returns_none(db: Any) -> None:
    """id inexistente → None (não erro): a rota traduz em staleness, não em 500."""
    assert knowledge.get_source(db, knowledge._rid("source", "ghost")) is None


def test_edit_source_updates_fields_keeping_id_and_history(db: Any) -> None:
    """O coração do #106: editar title/tags/canonical mantém o MESMO id, então a aresta
    from_source do item já coletado fica intacta — histórico preservado. Editar a URL não é
    'trocar por outra fonte': é 'esta fonte mudou de endereço', os itens seguem pendurados."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://old/feed", title="Antigo")
    item = knowledge.upsert_item(db, source=rid, external_id="ep-1", content="bruto")

    knowledge.edit_source(
        db, id=rid, title="Novo", tags=["python", "ml"], canonical="https://new/feed"
    )

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.id == rid  # id preservado
    assert got.title == "Novo"
    assert got.tags == ["python", "ml"]
    assert got.canonical == "https://new/feed"
    # o item segue ligado à MESMA source (histórico preservado)
    linked = db.query("SELECT ->from_source->source AS srcs FROM $i;", {"i": item})[0]["srcs"]
    assert linked == [rid]
    assert _count(db, "source") == 1


def test_edit_source_blank_title_becomes_none(db: Any) -> None:
    """Título vazio na edição limpa o campo (None), simétrico ao create — full-replace dos
    três campos editáveis, sem ambiguidade 'None = não mexer'."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed", title="Tinha")

    knowledge.edit_source(db, id=rid, title=None, tags=[], canonical="https://x/feed")

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.title is None


def test_edit_source_rejects_canonical_colliding_with_another(db: Any) -> None:
    """Editar a canonical para uma que JÁ existe em outro Cadastro do mesmo kind viola
    UNIQUE(kind, canonical): recusa como DuplicateSourceError (aviso soft), sem gravar."""
    a = knowledge.create_source(db, kind="rss", canonical="https://a/feed")
    knowledge.create_source(db, kind="rss", canonical="https://b/feed")

    with pytest.raises(DuplicateSourceError):
        knowledge.edit_source(db, id=a, title=None, tags=[], canonical="https://b/feed")

    # nada mudou: `a` ainda aponta para a canonical original
    got = knowledge.get_source(db, a)
    assert got is not None
    assert got.canonical == "https://a/feed"


def test_edit_source_keeping_own_canonical_is_not_a_duplicate(db: Any) -> None:
    """Editar só title/tags (canonical inalterada) NÃO dispara falso-positivo de duplicata:
    o lookup exclui o próprio record (senão o cadastro colidiria consigo mesmo)."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed", title="Antes")

    knowledge.edit_source(db, id=rid, title="Depois", tags=["a"], canonical="https://x/feed")

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.title == "Depois"
    assert got.tags == ["a"]


def test_edit_source_same_canonical_across_kinds_is_allowed(db: Any) -> None:
    """A colisão é por (kind, canonical): editar a canonical de um rss para uma que existe só
    num github-repo é permitido — kinds diferentes são Cadastros distintos."""
    rss = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    knowledge.create_source(db, kind="github-repo", canonical="https://github.com/o/r")

    knowledge.edit_source(db, id=rss, title=None, tags=[], canonical="https://github.com/o/r")

    got = knowledge.get_source(db, rss)
    assert got is not None
    assert got.canonical == "https://github.com/o/r"
    assert got.kind == "rss"  # kind NÃO muda na edição


def test_edit_source_on_archived_is_stale(db: Any) -> None:
    """Cadastro arquivado saiu do estado editável → StaleSourceError, sem escrita (belt
    `WHERE archived_at IS NONE`). (Arquivar é do #107; aqui semeamos archived_at cru.)"""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed", title="Antes")
    db.query("UPDATE $r SET enabled = false, archived_at = time::now();", {"r": rid})

    with pytest.raises(StaleSourceError):
        knowledge.edit_source(db, id=rid, title="Depois", tags=[], canonical="https://x/feed")

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.title == "Antes"  # nada gravado no cadastro arquivado


def test_edit_source_absent_is_stale(db: Any) -> None:
    """Editar um Cadastro que sumiu (hard-delete, #107) → StaleSourceError, nunca 500."""
    ghost = knowledge._rid("source", "ghost")
    with pytest.raises(StaleSourceError):
        knowledge.edit_source(db, id=ghost, title="x", tags=[], canonical="https://x")


# ── #107: pausar / arquivar / restaurar / apagar (ciclo de vida do Cadastro) ─────────────


def _state(db: Any, rid: RecordID) -> tuple[bool, str | None]:
    """Lê (enabled, archived_at) cru do banco — a prova de atomicidade do estado."""
    got = knowledge.get_source(db, rid)
    assert got is not None
    return got.enabled, got.archived_at


def test_set_source_enabled_pauses_without_archiving(db: Any) -> None:
    """Pausar é um estado PRÓPRIO (emenda #107 ao ADR-0025 §8): `enabled=false` com
    `archived_at=NONE` é VÁLIDO — o sweep varre só os ativos, então pausar tira do ar sem
    arquivar. Retomar volta a `enabled=true` sem nunca ter tocado `archived_at`."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")

    knowledge.set_source_enabled(db, id=rid, enabled=False)
    assert _state(db, rid) == (False, None)  # pausado, NÃO arquivado

    knowledge.set_source_enabled(db, id=rid, enabled=True)
    assert _state(db, rid) == (True, None)  # ativo de novo


def test_set_source_enabled_on_archived_is_stale(db: Any) -> None:
    """`enabled` de um arquivado é sempre `false` (invariante `archived_at set ⟹ enabled=false`):
    pausar/retomar um arquivado é recusado (StaleSourceError), sem violar o invariante."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    knowledge.archive_source(db, id=rid)
    archived_at_before = _state(db, rid)[1]  # captura archived_at ANTES da tentativa

    with pytest.raises(StaleSourceError):
        knowledge.set_source_enabled(db, id=rid, enabled=True)
    assert _state(db, rid) == (False, archived_at_before)  # segue arquivado/desabilitado


def test_set_source_enabled_absent_is_stale(db: Any) -> None:
    """Pausar um Cadastro inexistente → StaleSourceError, nunca 500."""
    with pytest.raises(StaleSourceError):
        knowledge.set_source_enabled(db, id=knowledge._rid("source", "ghost"), enabled=False)


def test_archive_source_sets_both_fields_atomically(db: Any) -> None:
    """Arquivar (ADR-0025 §8) põe `enabled=false` E `archived_at` num só statement — nunca o
    estado divergente arquivado-mas-ativo. O histórico (item pendurado) fica intacto: arquivar
    tira da operação, não apaga."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    item = knowledge.upsert_item(db, source=rid, external_id="ep-1", content="bruto")

    knowledge.archive_source(db, id=rid)

    enabled, archived = _state(db, rid)
    assert enabled is False
    assert archived is not None  # carimbo presente
    linked = db.query("SELECT ->from_source->source AS srcs FROM $i;", {"i": item})[0]["srcs"]
    assert linked == [rid]  # histórico preservado


def test_archive_source_already_archived_is_stale(db: Any) -> None:
    """Arquivar duas vezes: a segunda é StaleSourceError (WHERE archived_at IS NONE casa 0
    linhas), sem re-carimbar `archived_at` por cima do original."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    knowledge.archive_source(db, id=rid)
    first_stamp = _state(db, rid)[1]

    with pytest.raises(StaleSourceError):
        knowledge.archive_source(db, id=rid)
    assert _state(db, rid)[1] == first_stamp  # carimbo original intacto


def test_archive_source_absent_is_stale(db: Any) -> None:
    """Arquivar um Cadastro inexistente → StaleSourceError."""
    with pytest.raises(StaleSourceError):
        knowledge.archive_source(db, id=knowledge._rid("source", "ghost"))


def test_restore_source_clears_both_fields_atomically(db: Any) -> None:
    """Restaurar é o oposto exato de arquivar: `enabled=true` E `archived_at=NONE` num só
    statement. Volta ao estado ATIVO (não a pausado) — restaurar sempre reativa."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    knowledge.archive_source(db, id=rid)

    knowledge.restore_source(db, id=rid)

    assert _state(db, rid) == (True, None)  # ativo


def test_restore_source_not_archived_is_stale(db: Any) -> None:
    """Restaurar um Cadastro que NÃO está arquivado (ativo ou pausado) → StaleSourceError:
    o WHERE archived_at IS NOT NONE não casa nada, sem forçar enabled=true num pausado."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    knowledge.set_source_enabled(db, id=rid, enabled=False)  # pausado, não arquivado

    with pytest.raises(StaleSourceError):
        knowledge.restore_source(db, id=rid)
    assert _state(db, rid) == (False, None)  # segue pausado, restore não reativou


def test_delete_source_removes_cadastro_with_zero_items(db: Any) -> None:
    """Hard delete (ADR-0025 §8, exceção estreita): Cadastro com ZERO itens é apagado de vez —
    limpa um cadastro criado por engano. Só este caminho apaga na store inteira."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")

    knowledge.delete_source(db, id=rid)

    assert knowledge.get_source(db, rid) is None
    assert _count(db, "source") == 0


def test_delete_source_with_items_is_blocked(db: Any) -> None:
    """Apagar um Cadastro COM itens é impedido (SourceHasHistoryError) — proveniência é 'o
    produto', o caminho é arquivar. O record e o item seguem intactos."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    knowledge.upsert_item(db, source=rid, external_id="ep-1", content="bruto")

    with pytest.raises(SourceHasHistoryError):
        knowledge.delete_source(db, id=rid)

    assert knowledge.get_source(db, rid) is not None  # nada apagado
    assert _count(db, "source") == 1


def test_delete_source_archived_with_zero_items_is_allowed(db: Any) -> None:
    """A guarda do delete é ZERO ITENS, não o estado do ciclo de vida: um Cadastro arquivado
    (ou pausado) sem itens é apagável — não se exige um estado prévio (advisor)."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    knowledge.archive_source(db, id=rid)

    knowledge.delete_source(db, id=rid)

    assert knowledge.get_source(db, rid) is None


def test_delete_source_absent_is_stale(db: Any) -> None:
    """Apagar um Cadastro que já sumiu → StaleSourceError, nunca 500."""
    with pytest.raises(StaleSourceError):
        knowledge.delete_source(db, id=knowledge._rid("source", "ghost"))


def test_source_item_count_reflects_from_source_edges(db: Any) -> None:
    """`source_item_count` conta os itens via `<-from_source<-item`: 0 num cadastro novo, sobe a
    cada item coletado. É a guarda do apagável (zero) e o dado da tela de confirmação."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")
    assert knowledge.source_item_count(db, rid) == 0

    knowledge.upsert_item(db, source=rid, external_id="ep-1", content="a")
    knowledge.upsert_item(db, source=rid, external_id="ep-2", content="b")
    assert knowledge.source_item_count(db, rid) == 2


def test_sources_with_stats_carries_lifecycle_state(db: Any) -> None:
    """A listagem (#107) traz `enabled`/`archived_at` para a tela derivar o badge de estado e as
    ações: ativo, pausado (enabled=false, sem carimbo) e arquivado (com carimbo) são distinguíveis
    numa leitura só, sem N+1 get_source."""
    active = knowledge.create_source(db, kind="rss", canonical="https://active/feed")
    paused = knowledge.create_source(db, kind="rss", canonical="https://paused/feed")
    archived = knowledge.create_source(db, kind="rss", canonical="https://archived/feed")
    knowledge.set_source_enabled(db, id=paused, enabled=False)
    knowledge.archive_source(db, id=archived)

    by_id = {str(s.id): s for s in knowledge.sources_with_stats(db)}

    assert (by_id[str(active)].enabled, by_id[str(active)].archived_at) == (True, None)
    assert (by_id[str(paused)].enabled, by_id[str(paused)].archived_at) == (False, None)
    assert by_id[str(archived)].enabled is False
    assert by_id[str(archived)].archived_at is not None


def test_upsert_source_preserves_owner_edited_title(db: Any) -> None:
    """O coletor APENDE itens, não edita o Cadastro: um sweep (`upsert_source`) NÃO sobrescreve
    o título que o dono editou pela UI — senão a edição do #106 seria revertida todo dia, sem
    log. Cadastro SEM título ainda é preenchido pelo feed (nicety), e uma vez preenchido, fica."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed", title="Dono editou")

    knowledge.upsert_source(db, kind="rss", canonical="https://x/feed", title="Título do feed")

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.title == "Dono editou"  # coletor não clobbera


def test_upsert_source_fills_missing_title_from_feed(db: Any) -> None:
    """Cadastro sem título (criado pela UI sem informar) recebe o título do feed na 1ª coleta —
    a nicety legada sobrevive; só a SOBRESCRITA de um título já presente é que some."""
    rid = knowledge.create_source(db, kind="rss", canonical="https://x/feed")

    knowledge.upsert_source(db, kind="rss", canonical="https://x/feed", title="Título do feed")

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.title == "Título do feed"


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
    """dashboard_counts conta destilados, itens, fontes e entidades do acervo (Painel,
    4º StatTile de Entidades adicionado no retrofit M5)."""
    _seed_distilled(db, 3)  # cria 1 source + 3 items + 3 distilled
    knowledge.get_or_create_entity(db, name="Python")
    knowledge.get_or_create_entity(db, name="Rust")
    counts = knowledge.dashboard_counts(db)
    assert counts.distilled == 3
    assert counts.items == 3
    assert counts.sources == 1
    assert counts.entities == 2


def test_dashboard_counts_empty_acervo_is_zero(db: Any) -> None:
    """Acervo vazio: todas as contagens são 0, não erro."""
    counts = knowledge.dashboard_counts(db)
    assert (counts.distilled, counts.items, counts.sources, counts.entities) == (0, 0, 0, 0)


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


def test_list_runs_derives_repos_total_and_discovered_from_stats(db: Any) -> None:
    """D57: `repos_total`/`repos_discovered` (stats do github-releases) aparecem no card
    de run quando presentes -- o instrumento de verificação da migração REST->GraphQL
    (o dono confere `repos_total` contra a contagem real de watches)."""
    run = knowledge.start_run(db, worker="github-releases")
    knowledge.finish_run(db, run, stats={"repos_total": 261, "repos_discovered": 259, "items": 4})

    result = knowledge.list_runs(db, limit=20, start=0)[0]

    assert result.repos_total == 261
    assert result.repos_discovered == 259


def test_list_runs_repo_counts_are_none_when_absent(db: Any) -> None:
    """Workers que não descobrem repos (feed, distiller) não têm `repos_total`/
    `repos_discovered` em stats -- fallback gracioso pra None, mesmo padrão de `items`."""
    run = knowledge.start_run(db, worker="feed")
    knowledge.finish_run(db, run, stats={"items": 3})

    result = knowledge.list_runs(db, limit=20, start=0)[0]

    assert result.repos_total is None
    assert result.repos_discovered is None


def test_list_runs_pagination_start_skips(db: Any) -> None:
    """start pula as N execuções mais recentes — paginação estável."""
    for i in range(3):
        knowledge.finish_run(db, knowledge.start_run(db, worker=f"w{i}"))
    page2 = knowledge.list_runs(db, limit=2, start=2)
    assert [r.worker for r in page2] == ["w0"]


# ---------------------------------------------------------------------------
# Round 0011: busca + contagens (paginação), entidades do destilado, relacionados.
# ---------------------------------------------------------------------------


def _graph(db: Any) -> tuple[RecordID, RecordID]:
    """Grafo mínimo: 1 source + 1 item + 3 entidades + 2 destilados. Devolve (d1, d2)."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed", title="Feed")
    item = knowledge.upsert_item(db, source=src, external_id="e1", content="c", title="Post A")
    py = knowledge.get_or_create_entity(db, name="Python", kind="tecnologia")
    rust = knowledge.get_or_create_entity(db, name="Rust", kind="tecnologia")
    guido = knowledge.get_or_create_entity(db, name="Guido van Rossum", kind="pessoa")
    d1 = knowledge.insert_distilled(db, item=item, summary="py", chunks=[], entities=[py, guido])
    d2 = knowledge.insert_distilled(
        db, item=item, summary="py e rust", chunks=[], entities=[py, rust]
    )
    return d1, d2


def test_count_distilled_and_entities(db: Any) -> None:
    """count_distilled/count_entities dão o total do acervo (paginação sem busca)."""
    _graph(db)
    assert knowledge.count_distilled(db) == 2
    assert knowledge.count_entities(db) == 3


def test_list_entities_search_by_name_and_kind(db: Any) -> None:
    """list_entities(query=…) filtra por nome OU kind (busca de Entidades), e
    count_entities usa o MESMO filtro — o 'X de Y' não mente durante a busca."""
    _graph(db)
    by_name = knowledge.list_entities(db, limit=20, start=0, query="pyth")
    assert [e.name for e in by_name] == ["Python"]
    by_kind = {e.name for e in knowledge.list_entities(db, limit=20, start=0, query="pessoa")}
    assert by_kind == {"Guido van Rossum"}
    assert knowledge.count_entities(db, query="tecnologia") == 2  # Python + Rust
    assert knowledge.count_entities(db, query="pyth") == 1


def test_list_runs_search_by_worker_and_status(db: Any) -> None:
    """list_runs(query=…) filtra por worker OU status; count_runs usa o mesmo filtro."""
    knowledge.finish_run(db, knowledge.start_run(db, worker="feed"))  # status ok
    knowledge.fail_run(db, knowledge.start_run(db, worker="distiller"), error={"kind": "x"})
    assert [r.worker for r in knowledge.list_runs(db, limit=20, start=0, query="dist")] == [
        "distiller"
    ]
    assert knowledge.count_runs(db, query="error") == 1
    assert knowledge.count_runs(db) == 2


def test_read_distilled_includes_mentioned_entities(db: Any) -> None:
    """read_distilled passa a trazer as entidades mencionadas (chips do detalhe)."""
    d1, _ = _graph(db)
    view = knowledge.read_distilled(db, d1)
    assert view is not None
    names = {e.name for e in view.entities}
    assert names == {"Python", "Guido van Rossum"}
    kinds = {e.name: e.kind for e in view.entities}
    assert kinds["Guido van Rossum"] == "pessoa"


def test_related_distilled_shares_entity_excludes_self(db: Any) -> None:
    """related_distilled devolve destilados que compartilham entidade, SEM o próprio."""
    d1, d2 = _graph(db)  # d1 e d2 compartilham Python
    related = knowledge.related_distilled(db, d1, limit=10)
    ids = {str(c.id) for c in related}
    assert str(d2) in ids
    assert str(d1) not in ids  # nunca ele mesmo


# ---------------------------------------------------------------------------
# Sessão 0014 (A4): leituras read-only que alimentam a auditoria (B1) e o
# piloto (B2) do dreno — invariante 2 (nenhuma query crua no script one-off).
# `list_distilled_with_items` devolve o par summary×item + `run_worker` (o
# discriminador recente-vs-legado); `items_by_ids` busca o content por item id.
# ---------------------------------------------------------------------------


def test_items_by_ids_returns_id_title_content(db: Any) -> None:
    """items_by_ids devolve (id, title, content) dos itens pedidos — o piloto (B2)
    reenvia os MESMOS itens da amostra ao candidato, então precisa do content bruto
    por id, não da proveniência."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    a = knowledge.upsert_item(
        db, source=src, external_id="a", content="conteúdo A", title="Título A"
    )
    b = knowledge.upsert_item(db, source=src, external_id="b", content="conteúdo B")  # sem title

    by_id = {str(i): (t, c) for i, t, c in knowledge.items_by_ids(db, [a, b])}

    assert set(by_id) == {str(a), str(b)}
    assert by_id[str(a)] == ("Título A", "conteúdo A")
    assert by_id[str(b)] == (None, "conteúdo B")


def test_items_by_ids_empty_returns_empty(db: Any) -> None:
    """Lista de ids vazia devolve [] sem tocar o banco (não None, não erro)."""
    assert knowledge.items_by_ids(db, []) == []


def test_list_distilled_with_items_carries_summary_item_and_run_worker(db: Any) -> None:
    """Um distilled produzido por um run de worker `distiller` devolve
    (distilled_id, summary, item_id, created_at, run_worker="distiller") — o script
    da auditoria classifica RECENTE por `run_worker == "distiller"`."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item = knowledge.upsert_item(db, source=src, external_id="e1", content="c1")
    run = knowledge.start_run(db, worker="distiller")
    dist = knowledge.insert_distilled(db, item=item, summary="resumo recente", chunks=[], run=run)

    rows = knowledge.list_distilled_with_items(db, limit=10)

    assert len(rows) == 1
    d_id, summary, item_id, created_at, run_worker = rows[0]
    assert d_id == dist
    assert summary == "resumo recente"
    assert item_id == item
    assert created_at  # carimbo presente (string)
    assert run_worker == "distiller"


def test_list_distilled_with_items_legacy_without_run_has_none_worker(db: Any) -> None:
    """Um distilled SEM produced_by (legado do import Neon) devolve `run_worker is None`
    — o discriminador nunca crasha na ausência do run; o script trata None como legado."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item = knowledge.upsert_item(db, source=src, external_id="e1", content="c1")
    knowledge.insert_distilled(db, item=item, summary="resumo legado", chunks=[])

    rows = knowledge.list_distilled_with_items(db, limit=10)

    assert len(rows) == 1
    assert rows[0][4] is None


def test_list_distilled_with_items_empty_returns_empty(db: Any) -> None:
    """Sem destilados, a leitura é [] (não None, não erro)."""
    assert knowledge.list_distilled_with_items(db, limit=10) == []


def test_count_items_without_distilled_matches_the_filter(db: Any) -> None:
    """count_items_without_distilled conta os MESMOS candidatos de items_without_distilled
    (sem derived_from + content não-vazio) — métrica de progresso/reconciliação do dreno
    (0014), sem puxar o content de milhares de itens só para contá-los."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    knowledge.upsert_item(db, source=src, external_id="a", content="conteúdo A")
    knowledge.upsert_item(db, source=src, external_id="b", content="conteúdo B")
    knowledge.upsert_item(db, source=src, external_id="empty", content="   ")  # vazio: fora
    item_done = knowledge.upsert_item(db, source=src, external_id="c", content="conteúdo C")
    knowledge.insert_distilled(db, item=item_done, summary="já destilado", chunks=[])  # fora

    assert knowledge.count_items_without_distilled(db) == 2


def test_count_items_without_distilled_zero_when_none_pending(db: Any) -> None:
    """Banco sem candidato pendente conta 0 (não None, não erro)."""
    assert knowledge.count_items_without_distilled(db) == 0
