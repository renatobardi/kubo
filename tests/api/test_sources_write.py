"""Escrita de Fontes pela rota REAL (#105, molde ADR-0018): cadastrar uma fonte com a
credencial kubo_rw de verdade e LER DE VOLTA o grafo. Espelha o test_flows_write — se um
handler usasse por bug a conexão kubo_ro, a escrita falharia em silêncio e o cadastro
'daria certo' sem gravar; o read-back como root pega isso.

Integração: SurrealDB real + usuário kubo_rw EDITOR efêmero + app FastAPI real."""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.app import create_app
from kubo.store import client, migrations
from kubo.store.client import connect as _real_connect
from tests.api.conftest import UI_PASSWORD

pytestmark = pytest.mark.integration

_DB = "test_sources_write"
_RW_PASS = secrets.token_urlsafe(24)  # gerada por run — nunca um literal no repo (invariante 8)


@pytest.fixture
def app_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """App real apontado a um db efêmero com kubo_rw. A conftest stuba as leituras; a escrita
    (connect_rw) é a real, dirigida pelo SURREAL_DB + KUBO_RW_SURREAL_PASS do teste."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    # Restaura a conexão REAL: a conftest stuba client.connect por default (leituras), o que
    # envenenaria também connect_rw (que chama o mesmo `connect` do módulo) — como no flows_write.
    monkeypatch.setattr("kubo.store.client.connect", _real_connect)
    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        root.use(root_cfg.namespace, root_cfg.database)
        migrations.apply_migrations(root)
        # DEFINE USER + try juntos: uma falha não pode pular o finally e vazar kubo_rw no ROOT.
        root.query(f"DEFINE USER OVERWRITE kubo_rw ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")
        try:
            yield create_app()
        finally:
            root.query("REMOVE USER IF EXISTS kubo_rw ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _login_csrf(app: Any) -> tuple[TestClient, str]:
    """Autentica e devolve (client, csrf) lido do form 'Adicionar fonte'."""
    tc = TestClient(app)
    login = tc.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert login.status_code == 303
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', tc.get("/sources").text)
    assert m, "csrf ausente no form de Fontes"
    return tc, m.group(1)


def _sources(db_name: str = _DB) -> list[dict[str, Any]]:
    """Lê COMO ROOT as fontes gravadas — a prova do read-back."""
    with _real_connect(replace(client.config(), database=db_name)) as root:
        return root.query("SELECT kind, canonical, title, enabled FROM source;")


def test_create_via_real_route_lands_in_the_graph(app_db: Any) -> None:
    """Cadastrar pela rota real grava a fonte ATIVA no grafo (read-back como root). Se a rota
    usasse kubo_ro por bug, nada gravaria e o teste quebraria."""
    tc, csrf = _login_csrf(app_db)

    resp = tc.post(
        "/sources",
        data={
            "kind": "rss",
            "canonical": "https://feed.example/rss",
            "title": "Feed X",
            "tested": "1",
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303  # PRG
    rows = _sources()
    assert len(rows) == 1
    assert rows[0]["canonical"] == "https://feed.example/rss"
    assert rows[0]["title"] == "Feed X"
    assert rows[0]["enabled"] is True


def test_create_github_repo_normalizes_canonical(app_db: Any) -> None:
    """github-repo é normalizado à forma de-facto do worker `github_releases`
    (`https://github.com/{owner}/{name}`, sem barra final) — é isso que faz o coletor
    convergir na MESMA (kind, canonical) e reusar o record (lookup-first)."""
    tc, csrf = _login_csrf(app_db)

    resp = tc.post(
        "/sources",
        data={"kind": "github-repo", "canonical": "https://github.com/owner/repo/", "csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    rows = _sources()
    assert len(rows) == 1
    assert rows[0]["kind"] == "github-repo"
    assert rows[0]["canonical"] == "https://github.com/owner/repo"


def test_create_github_repo_accepts_short_form(app_db: Any) -> None:
    """A forma curta `owner/name` (que o placeholder do form anuncia) é aceita e normalizada
    à mesma canonical do worker — prova que o parsing estrutural preserva o atalho."""
    tc, csrf = _login_csrf(app_db)

    resp = tc.post(
        "/sources",
        data={"kind": "github-repo", "canonical": "owner/repo", "csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    rows = _sources()
    assert len(rows) == 1
    assert rows[0]["canonical"] == "https://github.com/owner/repo"


def test_duplicate_is_soft_warning_without_second_record(app_db: Any) -> None:
    """Duplicata (mesmo kind+canonical) reabre a tela com aviso SOFT (409) e NÃO grava um
    segundo record — a unicidade é garantida pela store, não pela view."""
    tc, csrf = _login_csrf(app_db)
    data = {"kind": "rss", "canonical": "https://dup.example/rss", "tested": "1", "csrf": csrf}

    first = tc.post("/sources", data=data, follow_redirects=False)
    assert first.status_code == 303

    again = tc.post("/sources", data=data, follow_redirects=False)
    assert again.status_code == 409
    assert "já está cadastrada" in again.text
    assert len(_sources()) == 1


def _edit_csrf(tc: TestClient, sid: str) -> str:
    """Lê o CSRF do form da página de edição de uma fonte específica."""
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', tc.get(f"/sources/{sid}/edit").text)
    assert m, "csrf ausente no form de edição"
    return m.group(1)


def _create_source_via_route(tc: TestClient, csrf: str, **data: str) -> str:
    """Cadastra pela rota real e devolve o KEY (parte do id) da fonte recém-criada.

    Preenche tested=1 por padrão para simular que o feed já passou pelo teste."""
    payload = {**data, "csrf": csrf}
    payload.setdefault("tested", "1")
    resp = tc.post("/sources", data=payload, follow_redirects=False)
    assert resp.status_code == 303
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rows = root.query("SELECT id FROM source WHERE canonical = $c;", {"c": data["canonical"]})
    return rows[0]["id"].id


def test_edit_via_real_route_updates_and_preserves_history(app_db: Any) -> None:
    """Editar pela rota real grava title/tags/canonical no MESMO record (id preservado) e o item
    já coletado segue ligado — histórico intacto (o coração do #106), provado por read-back."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(
        tc, csrf, kind="rss", canonical="https://old.example/feed", title="Antigo"
    )
    # semeia um item coletado sob essa fonte (histórico a preservar)
    with _real_connect(replace(client.config(), database=_DB)) as root:
        root.query(
            "RELATE (CREATE item SET external_id='ep-1', content='bruto')->from_source->$s;",
            {"s": RecordID("source", key)},
        )

    resp = tc.post(
        f"/sources/{key}/edit",
        data={
            "title": "Novo",
            "tags": "python, ml",
            "canonical": "https://new.example/feed",
            "csrf": _edit_csrf(tc, key),
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rows = root.query("SELECT canonical, title, tags FROM source;")
        linked = root.query(
            "SELECT count() FROM item WHERE ->from_source->source CONTAINS $s GROUP ALL;",
            {"s": RecordID("source", key)},
        )
    assert len(rows) == 1
    assert rows[0]["canonical"] == "https://new.example/feed"
    assert rows[0]["title"] == "Novo"
    assert rows[0]["tags"] == ["python", "ml"]
    assert linked and int(linked[0]["count"]) == 1  # item preservado sob a mesma fonte


def test_edit_github_repo_normalizes_canonical_per_db_kind(app_db: Any) -> None:
    """A canonical editada é normalizada pelo kind lido do BANCO (o form não manda kind): um
    github-repo com barra final vira a forma de-facto do worker. O kind não sai do form."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(
        tc, csrf, kind="github-repo", canonical="https://github.com/owner/repo"
    )

    resp = tc.post(
        f"/sources/{key}/edit",
        data={
            "title": "",
            "tags": "",
            "canonical": "https://github.com/owner/renamed/",
            "csrf": _edit_csrf(tc, key),
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    rows = _sources()
    assert rows[0]["canonical"] == "https://github.com/owner/renamed"


def test_edit_to_duplicate_canonical_is_soft_409(app_db: Any) -> None:
    """Editar a canonical para uma que já existe em OUTRO Cadastro do mesmo kind reabre com aviso
    SOFT (409) e NÃO grava — a unicidade (kind, canonical) é garantida pela store."""
    tc, csrf = _login_csrf(app_db)
    a_key = _create_source_via_route(tc, csrf, kind="rss", canonical="https://a.example/feed")
    _create_source_via_route(tc, csrf, kind="rss", canonical="https://b.example/feed")

    resp = tc.post(
        f"/sources/{a_key}/edit",
        data={
            "title": "",
            "tags": "",
            "canonical": "https://b.example/feed",
            "csrf": _edit_csrf(tc, a_key),
        },
        follow_redirects=False,
    )

    assert resp.status_code == 409
    with _real_connect(replace(client.config(), database=_DB)) as root:
        a_now = root.query("SELECT canonical FROM $s;", {"s": RecordID("source", a_key)})
    assert a_now[0]["canonical"] == "https://a.example/feed"  # inalterado


def test_edit_archived_source_is_stale_via_real_route(app_db: Any) -> None:
    """Fonte arquivada saiu do estado editável: o GET volta à lista (303) e o POST reabre com
    aviso de staleness (409), sem gravar. (Arquivar é do #107; aqui semeamos archived_at cru.)
    Cobre o guard de staleness do ADR-0018 §VI ponta-a-ponta, não só no nível da store."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(tc, csrf, kind="rss", canonical="https://arch.example/feed")
    with _real_connect(replace(client.config(), database=_DB)) as root:
        root.query(
            "UPDATE $s SET enabled = false, archived_at = time::now();",
            {"s": RecordID("source", key)},
        )

    get_resp = tc.get(f"/sources/{key}/edit", follow_redirects=False)
    assert get_resp.status_code == 303
    assert get_resp.headers["location"] == "/sources"

    post_resp = tc.post(
        f"/sources/{key}/edit",
        data={"title": "Novo", "tags": "", "canonical": "https://arch.example/feed", "csrf": csrf},
        follow_redirects=False,
    )
    assert post_resp.status_code == 409
    with _real_connect(replace(client.config(), database=_DB)) as root:
        row = root.query("SELECT title FROM $s;", {"s": RecordID("source", key)})
    assert row[0].get("title") is None  # nada gravado no cadastro arquivado


# ── #107: ciclo de vida pela rota REAL (pausar/arquivar/restaurar/apagar) ────────────────


def _state(key: str) -> dict[str, Any]:
    """Lê COMO ROOT (enabled, archived_at) de um Cadastro — a prova de estado do read-back."""
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rows = root.query("SELECT enabled, archived_at FROM $s;", {"s": RecordID("source", key)})
    return rows[0]


def test_disable_then_enable_via_real_route(app_db: Any) -> None:
    """Pausar pela rota real grava `enabled=false` SEM tocar `archived_at` (estado pausado é
    próprio — emenda #107); retomar volta a `enabled=true`. Read-back como root prova cada passo.
    PRG: POST retorna 303 com Location: /sources."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(tc, csrf, kind="rss", canonical="https://x.example/feed")

    resp = tc.post(f"/sources/{key}/disable", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/sources"
    st = _state(key)
    assert st["enabled"] is False and st.get("archived_at") is None  # pausado, não arquivado

    resp = tc.post(f"/sources/{key}/enable", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/sources"
    st = _state(key)
    assert st["enabled"] is True and st.get("archived_at") is None  # ativo de novo


def test_archive_then_restore_via_real_route(app_db: Any) -> None:
    """Arquivar pela rota real põe `enabled=false` E carimba `archived_at` (atômico); restaurar
    limpa os dois. Read-back como root — se a rota usasse kubo_ro por bug, nada gravaria.
    PRG: POST retorna 303 com Location: /sources."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(tc, csrf, kind="rss", canonical="https://x.example/feed")

    resp = tc.post(f"/sources/{key}/archive", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/sources"
    st = _state(key)
    assert st["enabled"] is False and st.get("archived_at") is not None  # arquivado

    resp = tc.post(f"/sources/{key}/restore", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/sources"
    st = _state(key)
    assert st["enabled"] is True and st.get("archived_at") is None  # ativo


def test_delete_zero_items_via_real_route(app_db: Any) -> None:
    """Apagar de vez uma fonte com ZERO itens pela rota real remove o record do grafo (303).
    O único caminho de delete da store, exercido ponta-a-ponta."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(tc, csrf, kind="rss", canonical="https://gone.example/feed")

    resp = tc.post(f"/sources/{key}/delete", data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 303
    assert len(_sources()) == 0  # apagado de vez


def test_delete_with_items_is_blocked_via_real_route(app_db: Any) -> None:
    """Apagar uma fonte COM itens é impedido pela rota real (409): o record e o item seguem
    intactos e a tela reorienta a arquivar (US#11). A guarda é da store, não da view."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(tc, csrf, kind="rss", canonical="https://keep.example/feed")
    with _real_connect(replace(client.config(), database=_DB)) as root:
        root.query(
            "RELATE (CREATE item SET external_id='ep-1', content='bruto')->from_source->$s;",
            {"s": RecordID("source", key)},
        )

    resp = tc.post(f"/sources/{key}/delete", data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 409
    assert "arquive" in resp.text.lower()  # reorienta a arquivar
    assert len(_sources()) == 1  # nada apagado


def test_delete_page_get_renders_confirmation_via_real_route(app_db: Any) -> None:
    """A tela de confirmação (GET) de uma fonte sem itens oferece o POST de apagar com CSRF —
    a dupla verificação servida pelo caminho real de leitura (kubo_ro)."""
    tc, csrf = _login_csrf(app_db)
    key = _create_source_via_route(tc, csrf, kind="rss", canonical="https://conf.example/feed")

    html = tc.get(f"/sources/{key}/delete").text

    assert f'action="/sources/{key}/delete"' in html
    assert "Apagar de vez" in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
