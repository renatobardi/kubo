"""KUBO-166 — Ativação + transições reversíveis: planning → scheduled → planning.

Testes de rota com store mockada. O COMPORTAMENTO de persistência vive nos
testes de integração da store; aqui ficam o molde das rotas (CSRF, sessão,
validação de estado) e a integração rota → store.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.study import Material, PlanEntry, StudyPlan, Topic

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
        "state": "planning",
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
        "status": "proposed",
        "weekdays": ["mon", "wed"],
        "target_date": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        "activated_at": None,
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
            sections=[RecordID("material_section", "s1")],
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
def stub_activate_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs comuns para rotas de ativação: store mockada."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.client.connect", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic())
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material()],
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries()),
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.activate_plan", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.deactivate_plan", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_state", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_all_chapters_light", lambda db, **kw: []
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_all_sections_light", lambda db, **kw: []
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_lessons_for_plan", lambda db, **kw: []
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_study_logs_for_plan", lambda db, **kw: {}
    )


# --- GET /topics/{key} (scheduled/running mostram plano) --------------------------------


def test_get_scheduled_shows_edit_plan_button(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET em scheduled renderiza o botão 'Editar plano' (plano visível)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="scheduled"),
    )
    html = authed_client.get(f"/study/topics/{_TOPIC_ID.id}").text
    assert "/edit-plan" in html
    assert "Editar plano" in html


def test_get_running_shows_frozen_message(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET em running renderiza mensagem de congelado (sem botão de editar)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="running"),
    )
    html = authed_client.get(f"/study/topics/{_TOPIC_ID.id}").text
    assert "congelado" in html
    assert "/edit-plan" not in html


def test_get_planning_shows_activate_button(authed_client: TestClient) -> None:
    """GET em planning renderiza o botão 'Ativar plano'."""
    html = authed_client.get(f"/study/topics/{_TOPIC_ID.id}").text
    assert "/activate" in html
    assert "Ativar plano" in html


# --- POST /topics/{key}/activate --------------------------------------------------------


def test_activate_transitions_planning_to_scheduled(authed_client: TestClient) -> None:
    """POST /activate em planning → 303 redirect (state=scheduled)."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/activate",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert f"/study/topics/{_TOPIC_ID.id}" in resp.headers["location"]


def test_activate_rejects_non_planning(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /activate em draft → 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="draft"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/activate",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_activate_rejects_running(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /activate em running → 400 (congelado)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="running"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/activate",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_activate_rejects_no_cadence(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /activate com plano sem weekdays → 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic",
        lambda db, **kw: (_plan(weekdays=[]), _entries()),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/activate",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_activate_rejects_bad_csrf(authed_client: TestClient) -> None:
    """POST /activate com CSRF inválido → 403."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/activate",
        data={"csrf": "deadbeef"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- POST /topics/{key}/edit-plan -------------------------------------------------------


def test_edit_plan_transitions_scheduled_to_planning(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /edit-plan em scheduled → 303 redirect (state=planning)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="scheduled"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/edit-plan",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert f"/study/topics/{_TOPIC_ID.id}" in resp.headers["location"]


def test_edit_plan_rejects_running(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /edit-plan em running → 400 (congelado, irreversível)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="running"),
    )
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/edit-plan",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_edit_plan_rejects_planning(authed_client: TestClient) -> None:
    """POST /edit-plan em planning → 400 (já em planning)."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/edit-plan",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400
