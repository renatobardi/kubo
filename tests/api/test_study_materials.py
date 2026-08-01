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
