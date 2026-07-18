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
    data = {"kind": "rss", "canonical": "https://dup.example/rss", "csrf": csrf}

    first = tc.post("/sources", data=data, follow_redirects=False)
    assert first.status_code == 303

    again = tc.post("/sources", data=data, follow_redirects=False)
    assert again.status_code == 409
    assert "já está cadastrada" in again.text
    assert len(_sources()) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
