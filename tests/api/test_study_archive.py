"""KUBO-167 — Arquivar/desarquivar/deletar + delete material bloqueado se congelado.

Testes de rota com store mockada. O COMPORTAMENTO de persistência vive nos
testes de integração da store; aqui ficam o molde das rotas (CSRF, sessão,
validação de estado) e a integração rota → store.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.study import (
    Material,
    PlanEntry,
    StudyPlan,
    Topic,
    TopicDeleteSummary,
    TopicProgress,
)

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_TOPIC_ID = RecordID("topic", "abc123")
_MATERIAL_ID = RecordID("material", "mat1")
_PLAN_ID = RecordID("study_plan", "plan1")


def _topic(**kw: object) -> Topic:
    base: dict[str, object] = {
        "id": _TOPIC_ID,
        "tenant_id": _TENANT,
        "user_id": _USER,
        "title": "Estudo de Agentic Coding",
        "state": "running",
        "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Topic(**base)  # type: ignore[arg-type]


def _material(**kw: object) -> Material:
    base: dict[str, object] = {
        "id": _MATERIAL_ID,
        "tenant_id": _TENANT,
        "user_id": _USER,
        "topic": _TOPIC_ID,
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


def _plan(**kw: object) -> StudyPlan:
    base: dict[str, object] = {
        "id": _PLAN_ID,
        "tenant_id": _TENANT,
        "user_id": _USER,
        "topic": _TOPIC_ID,
        "status": "active",
        "weekdays": ["mon", "wed"],
        "target_date": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        "activated_at": datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return StudyPlan(**base)  # type: ignore[arg-type]


def _entries() -> list[PlanEntry]:
    return [
        PlanEntry(
            id=RecordID("plan_entry", "e1"),
            study_plan=_PLAN_ID,
            tenant_id=_TENANT,
            user_id=_USER,
            seq=1,
            title="Lição 1",
            chapters=[RecordID("material_chapter", "c1")],
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        ),
    ]


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form da lista de Temas."""
    html = authed_client.get("/study/topics").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


@pytest.fixture(autouse=True)
def stub_archive_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs comuns para rotas de arquivar/deletar: store mockada."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.client.connect", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic())
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_archived_topics", lambda db, **kw: []
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material()],
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries()),
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.archive_topic", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.unarchive_topic", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.delete_topic", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic_delete_summary",
        lambda db, **kw: TopicDeleteSummary(
            materials=1, plan_entries=2, lessons=0, chat_messages=3
        ),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic_progress",
        lambda db, **kw: TopicProgress(done=0, total=2, next_lesson_id=None),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topics_progress_batch",
        lambda db, **kw: {},
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material",
        lambda db, **kw: _material(),
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.delete_material", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 1
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_state", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.revert_to_draft_if_planning", lambda db, **kw: True
    )


# --- POST /topics/{key}/archive ----------------------------------------------------------


def test_archive_transitions_to_archived(authed_client: TestClient) -> None:
    """POST /archive em running → 303 redirect."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/archive",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert f"/study/topics/{_TOPIC_ID.id}" in resp.headers["location"]


def test_archive_rejects_already_archived(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /archive em archived → 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="archived"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/archive",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_archive_rejects_bad_csrf(authed_client: TestClient) -> None:
    """POST /archive com CSRF inválido → 403."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/archive",
        data={"csrf": "deadbeef"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_archive_rejects_missing_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /archive em tema inexistente → 404."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/archive",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 404


# --- POST /topics/{key}/unarchive --------------------------------------------------------


def test_unarchive_restores_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /unarchive em archived → 303 redirect."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="archived"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/unarchive",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert f"/study/topics/{_TOPIC_ID.id}" in resp.headers["location"]


def test_unarchive_rejects_non_archived(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /unarchive em running → 400."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/unarchive",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_unarchive_rejects_bad_csrf(authed_client: TestClient) -> None:
    """POST /unarchive com CSRF inválido → 403."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/unarchive",
        data={"csrf": "deadbeef"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- POST /topics/{key}/delete -----------------------------------------------------------


def test_delete_topic_redirects_to_list(authed_client: TestClient) -> None:
    """POST /delete em running → 303 redirect para /topics."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/delete",
        data={"csrf": _csrf(authed_client), "confirm": "yes"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/study/topics" in resp.headers["location"]


def test_delete_topic_unlinks_material_files(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """POST /delete remove arquivos do volume (best-effort) e não quebra em OSError."""
    # Cria arquivo fake no tmp_path.
    fake_file = tmp_path / "manual.epub"
    fake_file.write_text("dummy")
    assert fake_file.exists()
    material_with_file = _material(file_path=str(fake_file))
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [material_with_file],
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/delete",
        data={"csrf": _csrf(authed_client), "confirm": "yes"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert not fake_file.exists()  # arquivo removido do volume.


def test_delete_rejects_without_confirmation(authed_client: TestClient) -> None:
    """POST /delete sem confirm=yes → 400 (confirmação reforçada)."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/delete",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_delete_rejects_bad_csrf(authed_client: TestClient) -> None:
    """POST /delete com CSRF inválido → 403."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/delete",
        data={"csrf": "deadbeef", "confirm": "yes"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- GET /topics/{key}/delete-confirm (dialog de confirmação) -----------------------------


def test_delete_confirm_shows_summary(authed_client: TestClient) -> None:
    """GET /topics/{key}/delete mostra contagem de dependentes (confirmação reforçada)."""
    html = authed_client.get(f"/study/topics/{_TOPIC_ID.id}/delete").text
    assert "1 material" in html or "1 materiais" in html
    assert "2 lição" in html or "2 lições" in html
    assert "3 mensagem" in html or "3 mensagens" in html


# --- delete_material bloqueado se congelado ----------------------------------------------


def test_delete_material_blocked_if_scheduled(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /materials/{mkey}/delete em scheduled → 400 (congelado)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="scheduled"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/materials/{_MATERIAL_ID.id}/delete",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_delete_material_blocked_if_running(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /materials/{mkey}/delete em running → 400 (congelado)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="running"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/materials/{_MATERIAL_ID.id}/delete",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_delete_material_blocked_if_archived(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /materials/{mkey}/delete em archived → 400 (só leitura)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="archived"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/materials/{_MATERIAL_ID.id}/delete",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_delete_material_allowed_in_planning(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /materials/{mkey}/delete em planning → 303 (permitido, exige regenerar plano)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="planning"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/materials/{_MATERIAL_ID.id}/delete",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303


# --- GET /topics enriquecida com progresso -----------------------------------------------


def test_list_topics_shows_progress(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /topics mostra progresso (lições feitas/total) de cada tema."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_topics",
        lambda db, **kw: [_topic(state="running")],
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topics_progress_batch",
        lambda db, **kw: {str(_TOPIC_ID): TopicProgress(done=3, total=10)},
    )
    html = authed_client.get("/study/topics").text
    assert "3/10" in html


# --- GET /topics?filter=archived ---------------------------------------------------------


def test_list_archived_topics(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /topics?filter=archived mostra só os arquivados."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_archived_topics",
        lambda db, **kw: [_topic(state="archived")],
    )
    html = authed_client.get("/study/topics?filter=archived").text
    assert "Estudo de Agentic Coding" in html
