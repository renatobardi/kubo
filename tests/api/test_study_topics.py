"""KUBO-161 — Tema vazio + lista de Temas (draft).

Testes de rota com a store mockada. O COMPORTAMENTO de persistência vive nos
testes de integração da store (`tests/store/test_study_topics.py`); aqui ficam
o molde das rotas (CSRF, sessão, PRG) e a renderização dos templates.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store.study import Topic

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")


def _topic(**kw: object) -> Topic:
    base: dict[str, object] = {
        "id": RecordID("topic", "abc123"),
        "tenant_id": _TENANT,
        "user_id": _USER,
        "title": "Estudo de Agentic Coding",
        "state": "draft",
        "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Topic(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def stub_study_topic_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leituras vazias por padrão; a store de study antiga é desacoplada."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_topic", lambda db, **kw: _topic())
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_name", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic", lambda db, **kw: []
    )


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form da lista de Temas."""
    html = authed_client.get("/study/topics").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


# --- Lista de Temas ----------------------------------------------------------------------


def test_topics_page_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert client.get("/study/topics", follow_redirects=False).status_code == 303


def test_topics_page_shows_empty_state(authed_client: TestClient) -> None:
    """Sem temas, mostra o estado vazio com botão 'Novo estudo'."""
    html = authed_client.get("/study/topics").text
    assert "Nenhum estudo ainda" in html
    assert "Novo estudo" in html


def test_topics_page_lists_topics_with_name_and_state(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lista mostra o nome e o estado de cada Tema."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_topics",
        lambda db, **kw: [
            _topic(title="Agentic Coding", state="draft"),
            _topic(title="Rust", state="running"),
        ],
    )
    html = authed_client.get("/study/topics").text
    assert "Agentic Coding" in html
    assert "Rust" in html
    assert "Rascunho" in html  # estado draft
    assert "Em andamento" in html  # estado running


# --- Criar Tema vazio --------------------------------------------------------------------


def test_create_topic_requires_csrf(authed_client: TestClient) -> None:
    """POST sem CSRF é 403."""
    resp = authed_client.post("/study/topics", follow_redirects=False)
    assert resp.status_code == 403


def test_create_topic_redirects_to_draft_page(authed_client: TestClient) -> None:
    """Criar Tema vazio redireciona pra tela do Tema em draft (303)."""
    csrf = _csrf(authed_client)
    resp = authed_client.post("/study/topics", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/study/topics/")


# --- Tela do Tema em draft ---------------------------------------------------------------


def test_topic_detail_shows_empty_draft_state(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema em draft sem Materiais mostra estado vazio guiado."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="draft"),
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "Adicione materiais" in html
    # Nome do Tema visível no topo
    assert "Estudo de Agentic Coding" in html


def test_topic_detail_404_for_missing_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema inexistente (ou de outro usuário) é 404, não 'negado'."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    resp = authed_client.get("/study/topics/naoexiste")
    assert resp.status_code == 404


# --- Editar nome do Tema -----------------------------------------------------------------


def test_rename_topic_requires_csrf(authed_client: TestClient) -> None:
    """POST de rename sem CSRF é 403."""
    resp = authed_client.post("/study/topics/abc123/rename", follow_redirects=False)
    assert resp.status_code == 403


def test_rename_topic_redirects_back(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renomear o Tema redireciona de volta pra tela do Tema (303)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="draft"),
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/rename",
        data={"csrf": csrf, "title": "Novo nome"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/study/topics/abc123" in resp.headers["location"]


def test_rename_topic_empty_title_rejected(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nome vazio é recusado (400)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="draft"),
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/rename",
        data={"csrf": csrf, "title": "   "},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_rename_archived_topic_rejected(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema arquivado é só leitura — rename devolve 503 (StoreError traduzido)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="archived"),
    )

    def _refuse(*args: object, **kwargs: object) -> None:
        raise StoreError("tema arquivado não pode ser renomeado")

    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_name", _refuse)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/rename",
        data={"csrf": csrf, "title": "Novo nome"},
        follow_redirects=False,
    )
    assert resp.status_code == 503
