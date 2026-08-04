"""KUBO-162 — Materiais no Tema: upload, lista, delete, limite.

Testes de rota com store/parsing/summarizer mockados. O COMPORTAMENTO de
persistência vive nos testes de integração da store; aqui ficam o molde das
rotas (CSRF, sessão, PRG, validação de limite) e a renderização do template.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.study import Material, Topic

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_TOPIC_ID = RecordID("topic", "abc123")


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


def _material(**kw: object) -> Material:
    base: dict[str, object] = {
        "id": RecordID("material", "mat1"),
        "tenant_id": _TENANT,
        "user_id": _USER,
        "topic": RecordID("topic", "abc123"),
        "title": "Manual de Kubo",
        "fmt": "epub",
        "original_filename": "manual.epub",
        "file_path": "/data/materials/manual.epub",
        "size_bytes": 1024,
        "chapter_count": 3,
        "summary": "Um guia sobre agentes.",
        "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Material(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def stub_study_material_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Leituras vazias por padrão; store mockado. Upload não faz parse/LLM (ADR-0049)."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_topic", lambda db, **kw: _topic())
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_name", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic", lambda db, **kw: []
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 0
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_pending_material",
        lambda db, **kw: _material(status="pending"),
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.delete_material", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])
    # Persona mockada: devolve um prompt stub + model (usado no chat do mentor).
    monkeypatch.setattr(
        "kubo.api.routes.study.resolve_persona",
        lambda *a, **kw: type(
            "P", (), {"prompt": "Resuma.", "model": "anthropic/claude-haiku-4-5"}
        )(),
    )
    # client.connect mockado para o _summarizer (chat do mentor).
    monkeypatch.setattr("kubo.api.routes.study.client.connect", _fake_connect)
    # Materials dir mockado: tmp_path.
    monkeypatch.setattr("kubo.api.routes.study._materials_dir", lambda: tmp_path)


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form da lista de Temas."""
    html = authed_client.get("/study/topics").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


# --- Tela do Tema com Materiais ----------------------------------------------------------


def test_topic_detail_shows_material_list(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tela do Tema em draft mostra a lista de Materiais adicionados."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [
            _material(title="Manual de Kubo"),
            _material(id=RecordID("material", "mat2"), title="Artigo complementar"),
        ],
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "Manual de Kubo" in html
    assert "Artigo complementar" in html


def test_topic_detail_shows_material_status_badge(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tela do Tema mostra badge de status de ingestão: Processando/Pronto/Falhou."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [
            _material(title="Pendente", status="pending"),
            _material(id=RecordID("material", "mat2"), title="Pronto", status="ready"),
            _material(
                id=RecordID("material", "mat3"),
                title="Falhado",
                status="failed",
                error="epub inválido",
            ),
        ],
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "Processando" in html
    assert "Pronto" in html
    assert "Falhou" in html
    assert "epub inválido" in html


def test_topic_detail_shows_retry_button_for_failed(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Material failed mostra botão 'Tentar de novo' (POST retry)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [
            _material(title="Falhado", status="failed", error="erro"),
        ],
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "Tentar de novo" in html
    assert "/materials/mat1/retry" in html


def test_topic_detail_polls_htmx_when_pending(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lista de materiais tem hx-trigger='every 5s' quando há material pending."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material(title="Pendente", status="pending")],
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "hx-trigger" in html
    assert "every 5s" in html


def test_topic_detail_no_polling_when_all_ready(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem pending, lista de materiais não faz polling HTMX."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material(title="Pronto", status="ready")],
    )
    html = authed_client.get("/study/topics/abc123").text
    # O elemento da lista não deve ter hx-trigger="every 5s".
    assert "every 5s" not in html


def test_topic_detail_shows_dropzone_in_draft(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tela do Tema em draft mostra a dropzone (não input file nativo)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "dropzone" in html.lower() or "drag" in html.lower()


def test_topic_detail_state_badge_has_semantic_color(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Badge de estado no detalhe tem cor semântica (D2)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="running")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: (None, [])
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "Em andamento" in html
    # running = emerald
    assert "bg-emerald-500/10" in html or "text-emerald-700" in html


def test_topic_detail_includes_study_chat_js(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tela do Tema inclui study-chat.js (B12 — JS unificado)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material()],
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "/static/study-chat.js" in html


def test_topic_detail_no_inline_sse_parser_duplicates(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem cópias inline do parser SSE (B12) — handleSSEEvent/handleMentorSSEEvent removidos."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material()],
    )
    html = authed_client.get("/study/topics/abc123").text
    # As 3 cópias inline devem desaparecer — o JS unificado está em study-chat.js.
    assert "function handleSSEEvent" not in html
    assert "function handleMentorSSEEvent" not in html


# --- Upload de Material -----------------------------------------------------------------


def test_upload_material_requires_csrf(authed_client: TestClient) -> None:
    """POST de upload sem CSRF é 403."""
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        files={"file": ("test.epub", b"data", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_upload_material_redirects_to_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload de epub redireciona pra tela do Tema (303) — cria Material pending."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    created: list[dict[str, object]] = []

    def _create_pending(db: object, **kw: object) -> Material:
        created.append(kw)
        return _material(title=str(kw.get("title", "x")), status="pending")

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_pending_material", _create_pending
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("manual.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/study/topics/abc123" in resp.headers["location"]
    assert len(created) == 1
    # Pending não tem chapters/sections/summary no request — zero LLM.
    assert "chapters" not in created[0]
    assert "summary" not in created[0]


def test_upload_material_rejects_bad_format(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arquivo não-epub/pdf é recusado (400)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("foto.jpg", b"fake jpg", "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_upload_material_rejects_non_draft_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload só em Tema draft — running/archived recusam (400)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="running")
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("manual.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_upload_material_rejects_over_limit(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema no limite de materiais recusa upload (400)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 5
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("manual.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_upload_material_404_for_missing_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload em Tema inexistente é 404."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/naoexiste/materials",
        data={"csrf": csrf},
        files={"file": ("manual.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_upload_multiple_files_creates_multiple_materials(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Múltiplos arquivos arrastados de uma vez viram múltiplos Materiais pending (AC#2)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    created: list[dict[str, object]] = []

    def _create_pending(db: object, **kw: object) -> Material:
        created.append(kw)
        return _material(title=str(kw.get("title", "x")), status="pending")

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_pending_material", _create_pending
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files=[
            ("file", ("manual.epub", b"fake epub 1", "application/epub+zip")),
            ("file", ("artigo.pdf", b"fake pdf 2", "application/pdf")),
        ],
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(created) == 2
    assert created[0]["fmt"] == "epub"
    assert created[1]["fmt"] == "pdf"


# --- Delete de Material -----------------------------------------------------------------


def test_delete_material_requires_csrf(authed_client: TestClient) -> None:
    """POST de delete sem CSRF é 403."""
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_delete_material_redirects_to_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deletar material redireciona pra tela do Tema (303)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/study/topics/abc123" in resp.headers["location"]


def test_delete_material_404_for_missing_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete em Tema inexistente é 404."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/naoexiste/materials/mat1/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_delete_material_rejects_non_draft_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete em Tema fora de draft é recusado (400) — Material imutável (ADR-0047 §4)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="running")
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400


# --- Delete do último Material em planning → auto-revert a draft (ADR-0047 Emenda 7) -----


def test_delete_last_material_in_planning_reverts_to_draft(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deletar o último Material em planning reverte o Tema a draft (Emenda 7).

    Sem Materiais, não há sobre o que propor — o estado planning é inválido.
    O auto-revert previne o estado em vez de tratar o crash em repropose.
    """
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="planning"),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 0
    )
    revert_calls: list[dict[str, object]] = []

    def _capture_revert(db: object, **kw: object) -> bool:
        revert_calls.append(kw)
        return True

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.revert_to_draft_if_planning", _capture_revert
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # O auto-revert chamou revert_to_draft_if_planning com o topic_id correto.
    assert len(revert_calls) == 1, f"esperado 1 chamada, got {revert_calls}"
    assert revert_calls[0].get("topic_id") == _TOPIC_ID, (
        f"topic_id incorreto: {revert_calls[0].get('topic_id')}"
    )
    # O redirect leva o notice exato de volta a draft.
    location = resp.headers["location"]
    assert "notice=voltou-rascunho" in location, f"notice ausente ou incorreto: {location}"


def test_delete_last_material_in_planning_cas_failed_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se o CAS falhar (estado mudou concorrentemente), devolve 400, não sobrescreve."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="planning"),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 0
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.revert_to_draft_if_planning",
        lambda db, **kw: False,
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "mudou" in resp.text.lower()


def test_delete_last_material_in_planning_shows_flash_on_topic_page(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tela do tema mostra o banner informativo quando o notice está no redirect."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="draft"),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic", lambda db, **kw: []
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])
    resp = authed_client.get("/study/topics/abc123", params={"notice": "voltou-rascunho"})
    assert resp.status_code == 200, f"esperado 200, got {resp.status_code}"
    # O banner mostra a mensagem humana, não a chave do notice.
    assert "Último material removido" in resp.text


def test_delete_non_last_material_in_planning_stays_in_planning(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deletar Material em planning quando há outros NÃO reverte a draft."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="planning"),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 1
    )
    revert_calls: list[dict[str, object]] = []

    def _capture_revert(db: object, **kw: object) -> bool:
        revert_calls.append(kw)
        return True

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.revert_to_draft_if_planning", _capture_revert
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # Não reverteu — revert_to_draft_if_planning não foi chamada.
    assert not revert_calls, f"revert chamada indevidamente: {revert_calls}"
    # Redirect sem notice.
    assert "notice=" not in resp.headers["location"]


# --- Retry de Material failed (KUBO-202, ADR-0049 §III) ----------------------------------


def test_retry_material_requires_csrf(authed_client: TestClient) -> None:
    """POST de retry sem CSRF é 403."""
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/retry",
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_retry_material_redirects_to_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry de material failed redireciona pra tela do Tema (303)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material",
        lambda db, **kw: _material(status="failed", error="erro"),
    )
    retried: list[dict[str, object]] = []
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.retry_material_ingest",
        lambda db, **kw: retried.append(kw) or _material(status="pending"),
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/retry",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/study/topics/abc123" in resp.headers["location"]
    assert len(retried) == 1


def test_retry_material_rejects_non_failed(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry de material ready/pending é 400 — só failed pode tentar de novo."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material",
        lambda db, **kw: _material(status="ready"),
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials/mat1/retry",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400


# --- KUBO-202: Upload não faz LLM (ADR-0049 §III) ----------------------------------------


def test_upload_does_not_call_summarizer_or_sectionizer(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload cria Material pending sem chamar summarizer/sectionizer (zero LLM no request)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    created: list[dict[str, object]] = []

    def _create_pending(db: object, **kw: object) -> Material:
        created.append(kw)
        return _material(title=str(kw.get("title", "x")), status="pending")

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_pending_material", _create_pending
    )

    # Summarizer.generate não deve ser chamado — se for, falha o teste.
    def _boom_generate(self: object, parsed: object) -> str:
        raise AssertionError("Summarizer.generate não deve ser chamado no upload")

    monkeypatch.setattr("kubo.api.routes.study.Summarizer.generate", _boom_generate)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("manual.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(created) == 1
    # Pending não recebe chapters/sections/summary.
    assert "chapters" not in created[0]
    assert "sections" not in created[0]
    assert "summary" not in created[0]


def test_upload_pending_title_from_filename(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Título do Material pending vem do nome do arquivo (sem extensão)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    created: list[dict[str, object]] = []

    def _create_pending(db: object, **kw: object) -> Material:
        created.append(kw)
        return _material(title=str(kw.get("title", "x")), status="pending")

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_pending_material", _create_pending
    )
    csrf = _csrf(authed_client)
    authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("manual-do-kubo.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert created[0]["title"] == "manual-do-kubo"
