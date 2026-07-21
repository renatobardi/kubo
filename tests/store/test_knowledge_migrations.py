"""Contrato do schema de conhecimento — migrations 0001/0002 (integração, SurrealDB).

Estes testes travam as decisões de DDL validadas pelo advisor (plano 0003 §3.1.5):
estrutura de tabelas/arestas, índice HNSW, FLEXIBLE materializado, arestas ENFORCED,
timestamps READONLY. São o guarda de regressão do schema, independente da store.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
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
    "destination",
    # 0012 (KUBO-44): singleton settings.
    "settings",
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
        "0007_deliverable_pr.surql",
        "0008_deliverable_merge_sha.surql",
        "0009_source_cadastro.surql",
        "0010_github_repo_cadastro.surql",
        "0011_destination_cadastro.surql",
        "0012_settings_singleton.surql",
        "0013_dispatch_destination_record.surql",
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


# ── 0009: source vira Cadastro (ADR-0025, ticket #104) ──────────────────────


def test_source_has_cadastro_fields(db: Any) -> None:
    """0009: source ganha os campos de estado do Cadastro (enabled/archived_at/tags)."""
    fields = db.query("INFO FOR TABLE source;")["fields"]
    assert "enabled" in fields
    assert "archived_at" in fields
    assert "tags" in fields


def test_source_unique_is_composite_kind_canonical(db: Any) -> None:
    """0009: unicidade passa de UNIQUE(canonical) para UNIQUE(kind, canonical)."""
    db.query("CREATE source:a SET kind='rss', canonical='https://x/feed';")
    # mesma canonical, kind diferente: agora é permitido (não era, sob o índice antigo).
    db.query("CREATE source:b SET kind='github-repo', canonical='https://x/feed';")
    # mesma canonical E mesmo kind: rejeitado pelo índice composto.
    with pytest.raises(Exception):  # noqa: B017, PT011 (índice UNIQUE do SurrealDB)
        db.query("CREATE source:c SET kind='rss', canonical='https://x/feed';")


def test_new_source_defaults_active_and_untagged(db: Any) -> None:
    """0009: Cadastro criado sem estado nasce enabled=true, tags=[], archived_at=NONE."""
    db.query("CREATE source:s SET kind='rss', canonical='https://x/feed';")
    got = db.query("SELECT enabled, tags, archived_at FROM source:s;")[0]
    assert got["enabled"] is True
    assert got["tags"] == []
    assert got.get("archived_at") is None


def test_legacy_source_backfilled_active(tmp_path: Path) -> None:
    """0009: registro criado ANTES da 0009 é backfillado para enabled=true, tags=[]."""
    # Aplica só até 0008 num dir temporário, insere um source "legado" (sem os campos
    # novos), e só então aplica a 0009 — é o cenário real dos dados de produção.
    pre = tmp_path / "pre"
    pre.mkdir()
    for f in sorted(migrations.MIGRATIONS_DIR.glob("*.surql")):
        if f.name < "0009":
            shutil.copy(f, pre / f.name)
    cfg = replace(client.config(), database="test_backfill_0009")
    with client.connect(cfg) as conn:
        conn.query("REMOVE DATABASE IF EXISTS test_backfill_0009;")
        try:
            conn.use(cfg.namespace, cfg.database)
            migrations.apply_migrations(conn, pre)
            conn.query("CREATE source:legacy SET kind='rss', canonical='https://x/legacy';")
            migrations.apply_migrations(conn)  # dir real: aplica a 0009 pendente
            got = conn.query("SELECT enabled, tags FROM source:legacy;")[0]
        finally:
            conn.query("REMOVE DATABASE IF EXISTS test_backfill_0009;")
    assert got["enabled"] is True
    assert got["tags"] == []


# ── 0010: GitHub vira Cadastro — source github-releases → github-repo (#110, ADR-0025 §5) ────

_0010 = "0010_github_repo_cadastro.surql"


def _apply_through_0009(conn: Any, tmp_path: Path) -> None:
    """Aplica migrations 0001..0009 (tudo ANTES da 0010) num dir temporário — o cenário real dos
    dados de produção no momento em que a 0010 vai rodar."""
    pre = tmp_path / "pre"
    pre.mkdir()
    for f in sorted(migrations.MIGRATIONS_DIR.glob("*.surql")):
        if f.name < _0010:
            shutil.copy(f, pre / f.name)
    migrations.apply_migrations(conn, pre)


def _run_0010_sql(conn: Any) -> None:
    """Executa o SQL da 0010 DIRETO (envolto em BEGIN/COMMIT como o runner faz) — para exercitar
    a própria idempotência da migração (o runner só a roda 1x; rodar o SQL 2x prova que os WHERE
    guards não apagam Cadastro legítimo nem mudam o sobrevivente numa 2ª passada)."""
    body = (migrations.MIGRATIONS_DIR / _0010).read_text(encoding="utf-8")
    conn.query(f"BEGIN;\n{body}\nCOMMIT;")


@pytest.fixture
def pre_0010_db(tmp_path: Path) -> Iterator[Any]:
    """DB com migrations até 0009 aplicadas — os testes inserem o cenário e então aplicam a 0010."""
    cfg = replace(client.config(), database="test_migr_0010")
    with client.connect(cfg) as conn:
        conn.query("REMOVE DATABASE IF EXISTS test_migr_0010;")
        conn.use(cfg.namespace, cfg.database)
        _apply_through_0009(conn, tmp_path)
        yield conn
        conn.query("REMOVE DATABASE IF EXISTS test_migr_0010;")


def test_0010_flips_github_releases_to_github_repo_keeping_provenance(pre_0010_db: Any) -> None:
    """Sem twin: um source github-releases (com item coletado) vira github-repo NO MESMO record —
    id e proveniência (aresta from_source do item) intactos, created_at (READONLY) preservado."""
    db = pre_0010_db
    db.query(
        "CREATE source:s1 SET kind='github-releases', "
        "canonical='https://github.com/o/r', title='o/r releases', enabled=true, tags=[];"
    )
    db.query("CREATE item:i1 SET external_id='1', content='c';")
    db.query("RELATE item:i1->from_source->source:s1;")
    created_before = db.query("SELECT created_at FROM source:s1;")[0]["created_at"]

    migrations.apply_migrations(db)  # aplica a 0010 pendente

    got = db.query("SELECT kind, canonical, created_at FROM source:s1;")[0]
    assert got["kind"] == "github-repo"
    assert got["canonical"] == "https://github.com/o/r"
    assert got["created_at"] == created_before  # READONLY, herda o piso `since` antigo
    items = db.query("SELECT array::len(<-from_source<-item) AS n FROM source:s1;")[0]["n"]
    assert items == 1


def test_0010_reconciles_twin_transferring_state_and_deleting_it(pre_0010_db: Any) -> None:
    """Com twin: o sobrevivente é o github-releases (tem a aresta); o twin github-repo criado pela
    UI (arquivado, com tags/título) é APAGADO, mas seu estado de Cadastro (enabled/archived_at/
    tags/title) transfere pro sobrevivente ANTES — a intenção do dono não se perde no flip."""
    db = pre_0010_db
    db.query(
        "CREATE source:survivor SET kind='github-releases', "
        "canonical='https://github.com/o/r', title='o/r releases', enabled=true, tags=[];"
    )
    db.query("CREATE item:i1 SET external_id='1', content='c';")
    db.query("RELATE item:i1->from_source->source:survivor;")
    # twin da UI: MESMA canonical, kind diferente → record distinto (edge-less), ARQUIVADO.
    db.query(
        "CREATE source:twin SET kind='github-repo', canonical='https://github.com/o/r', "
        "title='My Repo', tags=['ai'], enabled=false, archived_at=time::now();"
    )

    migrations.apply_migrations(db)

    assert db.query("SELECT id FROM source:twin;") == []  # twin apagado
    survivor = db.query("SELECT kind, title, tags, enabled, archived_at FROM source:survivor;")[0]
    assert survivor["kind"] == "github-repo"
    assert survivor["title"] == "My Repo"  # estado do twin transferido
    assert survivor["tags"] == ["ai"]
    assert survivor["enabled"] is False
    assert survivor["archived_at"] is not None  # arquivamento do dono preservado
    items = db.query("SELECT array::len(<-from_source<-item) AS n FROM source:survivor;")[0]["n"]
    assert items == 1  # proveniência intacta no sobrevivente
    # só UM record github-repo pra essa canonical (sem bifurcação residual)
    rows = db.query(
        "SELECT id FROM source WHERE kind='github-repo' AND canonical='https://github.com/o/r';"
    )
    assert len(rows) == 1


def test_0010_twin_merge_does_not_revert_a_pause_on_the_survivor(pre_0010_db: Any) -> None:
    """Achado do code-review: o sobrevivente github-releases TAMBÉM era pausável pela UI (#107 não
    filtra por kind). Se o dono pausou o SOBREVIVENTE e existe um twin github-repo ATIVO
    (enabled=true, default de criação #105), o merge NÃO pode reativar a coleta — pausa vence de
    qualquer lado (`enabled = enabled AND twin.enabled`). E tags que o dono pôs no sobrevivente não
    são apagadas por um twin de tags vazias."""
    db = pre_0010_db
    db.query(
        "CREATE source:sv SET kind='github-releases', canonical='https://github.com/o/r', "
        "title='old', enabled=false, tags=['keep'];"  # sobrevivente PAUSADO, com tags do dono
    )
    db.query(
        "CREATE source:tw SET kind='github-repo', canonical='https://github.com/o/r', "
        "title='New', tags=[], enabled=true, archived_at=NONE;"  # twin ATIVO, tags vazias
    )

    migrations.apply_migrations(db)

    got = db.query("SELECT kind, enabled, tags, title FROM source:sv;")[0]
    assert got["kind"] == "github-repo"
    assert (
        got["enabled"] is False
    )  # pausa do sobrevivente PRESERVADA (não revertida pelo twin ativo)
    assert got["tags"] == ["keep"]  # tags do dono não apagadas por twin de tags vazias
    assert got["title"] == "New"  # título do twin (não-nulo) adotado


def test_0010_preserves_untwinned_github_repo_cadastro(pre_0010_db: Any) -> None:
    """Um Cadastro github-repo criado pela UI SEM twin de coleta (repo que o dono cadastrou e o
    worker nunca coletou) NÃO é apagado — o DELETE do passo (2) só mira twins com sobrevivente."""
    db = pre_0010_db
    db.query(
        "CREATE source:only SET kind='github-repo', "
        "canonical='https://github.com/solo/repo', title='Solo', enabled=true, tags=['x'];"
    )

    migrations.apply_migrations(db)

    got = db.query("SELECT kind, canonical, tags FROM source:only;")
    assert len(got) == 1
    assert got[0]["kind"] == "github-repo"
    assert got[0]["tags"] == ["x"]


def test_0010_sql_is_self_idempotent(pre_0010_db: Any) -> None:
    """Rodar o SQL da 0010 uma 2ª vez (belt-and-suspenders sobre a guarda do runner) é no-op: não
    apaga o Cadastro github-repo legítimo nem mexe no sobrevivente já flipado."""
    db = pre_0010_db
    db.query(
        "CREATE source:s1 SET kind='github-releases', "
        "canonical='https://github.com/o/r', enabled=true, tags=[];"
    )
    db.query(
        "CREATE source:only SET kind='github-repo', "
        "canonical='https://github.com/solo/repo', enabled=true, tags=[];"
    )

    migrations.apply_migrations(db)  # 1ª aplicação da 0010
    _run_0010_sql(db)  # 2ª passada, direta

    assert db.query("SELECT kind FROM source:s1;")[0]["kind"] == "github-repo"
    assert db.query("SELECT id FROM source:only;") != []  # Cadastro legítimo intacto
