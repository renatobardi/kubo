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
from kubo.store.study import StudyPlan, Topic, TopicProgress

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
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic_progress",
        lambda db, **kw: TopicProgress(done=0, total=0),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topics_progress_batch",
        lambda db, **kw: {},
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_topic", lambda db, **kw: _topic())
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_name", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic", lambda db, **kw: []
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])


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
    """Sem temas, mostra o estado vazio com botão 'Novo tema'."""
    html = authed_client.get("/study/topics").text
    assert "Nenhum tema ainda" in html
    assert "Novo tema" in html


def test_error_dialog_present_in_page(authed_client: TestClient) -> None:
    """Toda página renderizada inclui o dialog global de erro.

    O dialog intercepta form POSTs via fetch e mostra respostas 4xx/5xx
    num <dialog> nativo em vez de navegar para texto cru.
    """
    html = authed_client.get("/study/topics").text
    assert 'id="kubo-error-dialog"' in html
    assert 'id="kubo-error-message"' in html
    # aria-labelledby liga o dialog ao h3 (acessibilidade — CodeRabbit).
    assert 'aria-labelledby="kubo-error-title"' in html


def test_error_dialog_skips_htmx_forms(authed_client: TestClient) -> None:
    """O interceptor pula forms com hx-post — HTMX gerencia o submit deles.

    Sem isso, o dashboard /auth/switch faria POST duplo (fetch + htmx).
    """
    html = authed_client.get("/study/topics").text
    # O JS do _error_dialog.html contém a guarda hasAttribute('hx-post').
    assert "hasAttribute('hx-post')" in html


def test_error_dialog_uses_formaction(authed_client: TestClient) -> None:
    """O interceptor respeita formAction do botão submitter (HTML spec)."""
    html = authed_client.get("/study/topics").text
    assert "ev.submitter && ev.submitter.formAction" in html


def test_plain_text_errors_return_text_plain_content_type(
    authed_client: TestClient,
) -> None:
    """Rotas que devolvem PlainTextResponse usam content-type text/plain.

    O interceptor JS só mostra o body no dialog se content-type for text/plain
    — JSON/HTML de handler de erro vira mensagem genérica. Este teste garante
    que o contrato entre rotas e dialog se mantém.
    """
    # POST sem CSRF → 403 PlainTextResponse
    resp = authed_client.post("/study/topics", data={}, follow_redirects=False)
    assert resp.status_code == 403
    ct = resp.headers.get("content-type", "")
    assert "text/plain" in ct


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


def test_topic_detail_planning_sr_only_inside_main(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug da sidebar que rola: `.sr-only` (position:absolute) tem que estar dentro
    de `<main>`, e `<main>` tem que ser `relative` + `overflow-y-auto`.

    O mecanismo: sem ancestral posicionado, o containing block de `.sr-only` é o
    viewport, e `overflow` só recorta descendentes cujo containing block está
    DENTRO do elemento. O label invisível escapava do clipping, esticava o
    documento e a sidebar rolava junto. A prova de behavior real (scrollHeight ==
    clientHeight) foi feita via Chrome headless CDP; no CI, o que dá a provar é
    que a estrutura causadora está correta: `.sr-only` dentro de `<main>` com
    `relative` + `overflow-y-auto`.
    """
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="planning"),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic",
        lambda db, **kw: (
            StudyPlan(
                id=RecordID("study_plan", "p1"),
                tenant_id=_TENANT,
                user_id=_USER,
                topic=RecordID("topic", "abc123"),
                status="proposed",
                weekdays=[],
                target_date=None,
                activated_at=None,
                created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            ),
            [],
        ),
    )
    monkeypatch.setattr("kubo.api.routes.study._collect_all_chapters", lambda *a, **kw: [])

    html = authed_client.get("/study/topics/abc123").text

    main_start = html.find("<main")
    main_end = html.find("</main>") + len("</main>")
    assert main_start != -1 and main_end > main_start, "página sem <main>"

    main_tag = html[main_start : html.find(">", main_start) + 1]
    assert "relative" in main_tag, f"<main> sem `relative`: {main_tag}"
    assert "overflow-y-auto" in main_tag, f"<main> sem `overflow-y-auto`: {main_tag}"

    # `.sr-only` (planner chat label) tem que estar dentro de <main>.
    sr_only = html.find("sr-only")
    assert sr_only != -1, "study/topic sem .sr-only — teste não exercita o bug"
    assert main_start < sr_only < main_end, ".sr-only fora do <main>: absolutos escapam"


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
    """Tema arquivado é só leitura — rename devolve 400 (StoreError = regra de negócio)."""
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
    assert resp.status_code == 400
