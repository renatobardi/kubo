"""KUBO-164 — Fechar Tema: transição draft → planning + planner propõe Plano.

Testes de rota com store/mentor/planner mockados. O COMPORTAMENTO de persistência
vive nos testes de integração da store; aqui ficam o molde das rotas (CSRF,
sessão, validação de estado) e a integração rota → planner → store.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.study import Material, PlanEntry, StudyPlan, Topic
from kubo.study.planner import PlanLesson, PlanProposal

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
        "state": "draft",
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


def _plan() -> StudyPlan:
    return StudyPlan(
        id=_PLAN_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC_ID,
        status="proposed",
        weekdays=[],
        target_date=None,
        activated_at=None,
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


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
def stub_close_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs comuns para rotas de close: store mockado, persona mockada."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.client.connect", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic())
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 1
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material()],
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_state", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.save_plan_proposal",
        lambda db, **kw: (_plan(), _entries()),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries()),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.resolve_persona",
        lambda *a, **kw: type(
            "P", (), {"prompt": "Você é o planner.", "model": "anthropic/claude-opus-5"}
        )(),
    )

    # Planner mockado: devolve proposta fixa.
    def _fake_propose(self: Any, sections: Any, **kw: Any) -> PlanProposal:
        return PlanProposal(
            lessons=[PlanLesson(title="Lição 1", sections=[(1, 1), (1, 2), (2, 1)])]
        )

    monkeypatch.setattr("kubo.study.planner.Planner.propose", _fake_propose)

    # list_chapters: devolve capítulos fake (para _collect_all_sections).
    from kubo.store.study import MaterialChapter

    def _fake_list_chapters(db: Any, **kw: Any) -> list[MaterialChapter]:
        return [
            MaterialChapter(
                id=RecordID("material_chapter", f"c{i}"),
                material=_MATERIAL_ID,
                seq=i,
                title=f"Capítulo {i}",
                part=None,
                content=f"Conteúdo {i}.",
            )
            for i in range(1, 4)
        ]

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_all_chapters_light", _fake_list_chapters
    )

    # list_all_sections: devolve seções fake com chapter_seq global.
    from kubo.store.study import MaterialSection

    def _fake_list_sections(db: Any, **kw: Any) -> list[MaterialSection]:
        return [
            MaterialSection(
                id=RecordID("material_section", f"s{i}"),
                material=_MATERIAL_ID,
                material_chapter=RecordID("material_chapter", f"c{(i + 1) // 2}"),
                seq=(i % 2) or 2,
                title=f"Seção {i}",
                anchor_text="",
                content="",
                summary=f"Sumário {i}",
                chapter_seq=(i + 1) // 2,
            )
            for i in range(1, 7)
        ]

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_all_sections_light", _fake_list_sections
    )


def test_close_topic_transitions_to_planning(authed_client: TestClient) -> None:
    """POST /close transiciona draft → planning e persiste proposta."""
    resp = authed_client.post(
        "/study/topics/abc123/close",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/study/topics/abc123"


def test_close_topic_without_materials_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fechar tema sem materiais devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_materials_by_topic", lambda db, **kw: 0
    )
    resp = authed_client.post(
        "/study/topics/abc123/close",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_close_topic_not_draft_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fechar tema que não está em draft devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="planning"),
    )
    resp = authed_client.post(
        "/study/topics/abc123/close",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_close_topic_not_found_returns_404(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema inexistente devolve 404."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    resp = authed_client.post(
        "/study/topics/abc123/close",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_close_topic_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/close",
        data={"csrf": "invalid"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_close_topic_falls_back_to_mechanical_when_planner_fails(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planner que devolve None cai no mechanical_proposal (não trava a tela)."""
    monkeypatch.setattr("kubo.study.planner.Planner.propose", lambda self, sections, **kw: None)
    resp = authed_client.post(
        "/study/topics/abc123/close",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303


# --- Template: botão Fechar + visualização do plano -------------------------------------


def test_topic_detail_draft_with_materials_shows_close_button(
    authed_client: TestClient,
) -> None:
    """Tema em draft com materiais mostra botão 'Fechar tema'."""
    html = authed_client.get("/study/topics/abc123").text
    assert "Fechar tema" in html
    assert "/close" in html


def test_topic_detail_draft_without_materials_hides_close_button(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema em draft SEM materiais NÃO mostra botão 'Fechar tema'."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [],
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "Fechar tema" not in html


def test_topic_detail_planning_shows_plan(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema em planning mostra o plano com lições e cadência."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic",
        lambda db, **kw: _topic(state="planning"),
    )
    html = authed_client.get("/study/topics/abc123").text
    assert "Plano de estudo" in html
    assert "Lição 1" in html
    assert "Seção 1" in html  # section title rendered
