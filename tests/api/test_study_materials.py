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

from kubo.errors import StoreError
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
    """Leituras vazias por padrão; parsing e summarizer mockados."""
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
    monkeypatch.setattr("kubo.api.routes.study.study_store.delete_material", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])
    # Parsing mockado: devolve um ParsedMaterial simples.
    from kubo.study.parsing import ParsedChapter, ParsedMaterial

    def _fake_parse(data: bytes, fmt: str) -> ParsedMaterial:
        return ParsedMaterial(
            title="Manual de Kubo",
            chapters=[ParsedChapter(seq=0, title="Cap 1", content="Conteúdo.", part=None)],
        )

    monkeypatch.setattr("kubo.api.routes.study.parse_material", _fake_parse)
    # Summarizer mockado: devolve string fixa.
    monkeypatch.setattr(
        "kubo.api.routes.study.Summarizer.generate", lambda self, parsed: "Um guia sobre agentes."
    )
    # Persona mockada: devolve um prompt stub + model.
    monkeypatch.setattr(
        "kubo.api.routes.study.resolve_persona",
        lambda *a, **kw: type(
            "P", (), {"prompt": "Resuma.", "model": "anthropic/claude-haiku-4-5"}
        )(),
    )
    # client.connect mockado para o _summarizer.
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


def test_topic_detail_shows_dropzone_in_draft(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tela do Tema em draft mostra a dropzone (não input file nativo)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "dropzone" in html.lower() or "drag" in html.lower()


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
    """Upload de epub redireciona pra tela do Tema (303)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
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
    """Múltiplos arquivos arrastados de uma vez viram múltiplos Materiais (AC#2)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    created: list[dict[str, object]] = []

    def _create(db: object, **kw: object) -> Material:
        created.append(kw)
        return _material(title=str(kw.get("title", "x")))

    monkeypatch.setattr("kubo.api.routes.study.study_store.create_material", _create)
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


# --- KUBO-184: Sectionizer no upload ----------------------------------------------------


def test_upload_calls_sectionizer_and_passes_sections_to_store(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload chama sectionize e passa o dict de seções para create_material."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    created: list[dict[str, object]] = []

    def _create(db: object, **kw: object) -> Material:
        created.append(kw)
        return _material(title=str(kw.get("title", "x")))

    monkeypatch.setattr("kubo.api.routes.study.study_store.create_material", _create)
    # Sectionize mockado: devolve dict com 1 seção por capítulo.
    from kubo.study.parsing import SectionPart

    def _fake_sectionize(*, executor, prompt, chapters):
        return {
            ch.seq: [
                SectionPart(title=ch.title, anchor_text="", content=ch.content, summary=ch.title)
            ]
            for ch in chapters
        }

    monkeypatch.setattr("kubo.api.routes.study.sectionize", _fake_sectionize)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("manual.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(created) == 1
    # sections foi passado e não é None.
    assert created[0]["sections"] is not None
    assert isinstance(created[0]["sections"], dict)


def test_upload_succeeds_when_sectionizer_setup_fails(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sectionizer setup falha → upload continua com sections=None (fallback na store)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    created: list[dict[str, object]] = []

    def _create(db: object, **kw: object) -> Material:
        created.append(kw)
        return _material(title=str(kw.get("title", "x")))

    monkeypatch.setattr("kubo.api.routes.study.study_store.create_material", _create)

    # _sectionizer_executor levanta StoreError — simula persona ausente no catálogo.
    def _boom(ctx):
        raise StoreError("sectionizer persona not found")

    monkeypatch.setattr("kubo.api.routes.study._sectionizer_executor", _boom)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/study/topics/abc123/materials",
        data={"csrf": csrf},
        files={"file": ("manual.epub", b"fake epub", "application/epub+zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(created) == 1
    # sections é None — a store faz fallback (1 seção por capítulo).
    assert created[0]["sections"] is None
