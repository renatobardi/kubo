"""KUBO-165 — Fase 2: chat com planner + edição incremental + voltar/repropor.

Testes de rota com store/planner mockados. O COMPORTAMENTO de persistência
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
            chapters=[RecordID("material_chapter", "c1")],
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        ),
        PlanEntry(
            id=RecordID("plan_entry", "e2"),
            study_plan=_PLAN_ID,
            tenant_id=_TENANT,
            user_id=_USER,
            seq=2,
            title="Lição 2",
            chapters=[RecordID("material_chapter", "c2"), RecordID("material_chapter", "c3")],
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
def stub_planner_chat_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs comuns para rotas de planner-chat: store mockado, persona mockada."""
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
    monkeypatch.setattr("kubo.api.routes.study.tenancy.assert_membership", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_chat_message", lambda db, **kw: None
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.save_plan_proposal",
        lambda db, **kw: (_plan(), _entries()),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.replace_plan_entries",
        lambda db, **kw: (_plan(), _entries()),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries()),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.swap_plan_entries", lambda db, **kw: None
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.remove_chapter_from_entry", lambda db, **kw: True
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_plan_cadence", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.resolve_persona",
        lambda *a, **kw: type(
            "P", (), {"prompt": "Você é o planner.", "model": "anthropic/claude-opus-5"}
        )(),
    )

    # Planner.stream_chat mockado: devolve chunks que formam texto + bloco JSON.
    def _fake_stream_chat(self: Any, executor: Any, **kw: Any) -> Any:
        yield "Juntei as lições 1 e 2."
        yield '\n\n```json\n{"lessons": [{"title": "Tudo junto", "chapter_seqs": [1, 2, 3]}]}\n```'

    monkeypatch.setattr("kubo.study.planner.Planner.stream_chat", _fake_stream_chat)

    # Planner.propose mockado para repropose.
    def _fake_propose(self: Any, chapters: Any, **kw: Any) -> PlanProposal:
        return PlanProposal(lessons=[PlanLesson(title="Lição 1", chapter_seqs=[1, 2, 3])])

    monkeypatch.setattr("kubo.study.planner.Planner.propose", _fake_propose)

    # list_all_chapters_light: devolve capítulos fake (sem content).
    from kubo.store.study import MaterialChapter

    def _fake_list_chapters(db: Any, **kw: Any) -> list[MaterialChapter]:
        return [
            MaterialChapter(
                id=RecordID("material_chapter", f"c{i}"),
                material=_MATERIAL_ID,
                seq=i,
                title=f"Capítulo {i}",
                part=None,
                content="",
            )
            for i in range(1, 4)
        ]

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_all_chapters_light", _fake_list_chapters
    )


# --- POST /topics/{key}/planner-chat (SSE) -----------------------------------------------


def test_planner_chat_returns_sse_stream(authed_client: TestClient) -> None:
    """POST /planner-chat devolve SSE com chunks + done."""
    resp = authed_client.post(
        "/study/topics/abc123/planner-chat",
        data={"csrf": _csrf(authed_client), "message": "junta lição 1 e 2"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    body = resp.text
    assert "event: done" in body
    assert "plan_updated" in body


def test_planner_chat_empty_message_returns_400(authed_client: TestClient) -> None:
    """Mensagem vazia devolve 400."""
    resp = authed_client.post(
        "/study/topics/abc123/planner-chat",
        data={"csrf": _csrf(authed_client), "message": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_planner_chat_not_planning_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat com planner fora de planning devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    resp = authed_client.post(
        "/study/topics/abc123/planner-chat",
        data={"csrf": _csrf(authed_client), "message": "oi"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_planner_chat_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/planner-chat",
        data={"csrf": "invalid", "message": "oi"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_planner_chat_persists_assistant_message(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resposta do planner é persistida como assistant message (phase=planning)."""
    persisted: list[dict[str, Any]] = []

    def _capture_create(db: Any, **kw: Any) -> Any:
        persisted.append(kw)
        return None

    monkeypatch.setattr("kubo.api.routes.study.study_store.create_chat_message", _capture_create)
    authed_client.post(
        "/study/topics/abc123/planner-chat",
        data={"csrf": _csrf(authed_client), "message": "junta"},
        follow_redirects=False,
    )
    # Pelo menos 2 chamadas: user message + assistant message.
    assert len(persisted) >= 2
    assert persisted[0]["role"] == "user"
    assert persisted[0]["phase"] == "planning"
    assert persisted[1]["role"] == "assistant"
    assert persisted[1]["phase"] == "planning"


# --- POST /topics/{key}/back-to-draft ----------------------------------------------------


def test_back_to_draft_transitions_planning_to_draft(authed_client: TestClient) -> None:
    """POST /back-to-draft transiciona planning → draft."""
    resp = authed_client.post(
        "/study/topics/abc123/back-to-draft",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/study/topics/abc123"


def test_back_to_draft_not_planning_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Voltar pra draft fora de planning devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    resp = authed_client.post(
        "/study/topics/abc123/back-to-draft",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_back_to_draft_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/back-to-draft",
        data={"csrf": "invalid"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- POST /topics/{key}/repropose --------------------------------------------------------


def test_repropose_regenerates_plan(authed_client: TestClient) -> None:
    """POST /repropose regenera o plano do zero e redireciona."""
    resp = authed_client.post(
        "/study/topics/abc123/repropose",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/study/topics/abc123"


def test_repropose_not_planning_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repropor fora de planning devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="draft")
    )
    resp = authed_client.post(
        "/study/topics/abc123/repropose",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_repropose_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/repropose",
        data={"csrf": "invalid"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_repropose_with_no_chapters_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repropor com 0 capítulos (Materiais deletados) devolve 400, não 500.

    Defesa em profundidade: mesmo se o auto-revert (Emenda 7) falhar, repropose
    não crasha com ValidationError em mechanical_proposal([]) — a rota guarda
    cedo e devolve mensagem legível.
    """
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_all_chapters_light", lambda db, **kw: []
    )
    resp = authed_client.post(
        "/study/topics/abc123/repropose",
        data={"csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "materiais" in resp.text.lower()


# --- POST /topics/{key}/plan/entries/{ekey}/move -----------------------------------------


def test_move_entry_up_redirects(authed_client: TestClient) -> None:
    """POST /plan/entries/{ekey}/move direction=up troca com vizinho de cima."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e2/move",
        data={"csrf": _csrf(authed_client), "direction": "up"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_move_entry_down_redirects(authed_client: TestClient) -> None:
    """POST /plan/entries/{ekey}/move direction=down troca com vizinho de baixo."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e1/move",
        data={"csrf": _csrf(authed_client), "direction": "down"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_move_entry_invalid_direction_returns_400(authed_client: TestClient) -> None:
    """Direção inválida devolve 400."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e1/move",
        data={"csrf": _csrf(authed_client), "direction": "sideways"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_move_entry_not_planning_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Move fora de planning devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="running")
    )
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e1/move",
        data={"csrf": _csrf(authed_client), "direction": "down"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_move_entry_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e1/move",
        data={"csrf": "invalid", "direction": "down"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- POST /topics/{key}/plan/entries/{ekey}/remove-chapter -------------------------------


def test_remove_chapter_redirects(authed_client: TestClient) -> None:
    """POST /plan/entries/{ekey}/remove-chapter remove capítulo e redireciona."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e2/remove-chapter",
        data={"csrf": _csrf(authed_client), "chapter_id": "material_chapter:c3"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_remove_chapter_invalid_id_returns_400(authed_client: TestClient) -> None:
    """chapter_id malformado devolve 400 (não 500)."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e2/remove-chapter",
        data={"csrf": _csrf(authed_client), "chapter_id": "no-colon"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_remove_chapter_not_planning_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove chapter fora de planning devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="running")
    )
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e2/remove-chapter",
        data={"csrf": _csrf(authed_client), "chapter_id": "material_chapter:c3"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_remove_chapter_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/entries/e2/remove-chapter",
        data={"csrf": "invalid", "chapter_id": "material_chapter:c3"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- POST /topics/{key}/plan/cadence -----------------------------------------------------


def test_set_cadence_redirects(authed_client: TestClient) -> None:
    """POST /plan/cadence define dias e redireciona."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/cadence",
        data={"csrf": _csrf(authed_client), "weekdays": ["mon", "wed", "fri"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_set_cadence_not_planning_returns_400(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set cadence fora de planning devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="running")
    )
    resp = authed_client.post(
        "/study/topics/abc123/plan/cadence",
        data={"csrf": _csrf(authed_client), "weekdays": ["mon"]},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_set_cadence_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/plan/cadence",
        data={"csrf": "invalid", "weekdays": ["mon"]},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# --- Template: chat do planner + botões --------------------------------------------------


def test_topic_detail_planning_shows_planner_chat(authed_client: TestClient) -> None:
    """Tema em planning mostra seção de chat com planner."""
    html = authed_client.get("/study/topics/abc123").text
    assert "Conversa com o planner" in html
    assert "/planner-chat" in html


def test_topic_detail_planning_shows_back_to_draft_button(authed_client: TestClient) -> None:
    """Tema em planning mostra botão 'Voltar'."""
    html = authed_client.get("/study/topics/abc123").text
    assert "/back-to-draft" in html
    assert "Voltar" in html


def test_topic_detail_planning_shows_repropose_button(authed_client: TestClient) -> None:
    """Tema em planning mostra botão 'Repropor tudo'."""
    html = authed_client.get("/study/topics/abc123").text
    assert "/repropose" in html
    assert "Repropor" in html


def test_topic_detail_planning_shows_cadence_form(authed_client: TestClient) -> None:
    """Tema em planning mostra formulário de cadência."""
    html = authed_client.get("/study/topics/abc123").text
    assert "/plan/cadence" in html


def test_topic_detail_planning_shows_planning_chat_history(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Histórico do chat com planner (phase=planning) aparece na tela."""
    from kubo.store.study import ChatMessage

    def _fake_messages(db: Any, **kw: Any) -> list[ChatMessage]:
        phase = kw.get("phase", "")
        if phase != "planning":
            return []
        return [
            ChatMessage(
                id=RecordID("study_chat", "m1"),
                tenant_id=_TENANT,
                user_id=_USER,
                topic=_TOPIC_ID,
                phase="planning",
                role="user",
                content="Junta tudo numa lição",
                created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            ),
            ChatMessage(
                id=RecordID("study_chat", "m2"),
                tenant_id=_TENANT,
                user_id=_USER,
                topic=_TOPIC_ID,
                phase="planning",
                role="assistant",
                content="Ok, juntei.",
                created_at=datetime(2026, 8, 1, 12, 1, tzinfo=timezone.utc),
            ),
        ]

    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", _fake_messages)
    html = authed_client.get("/study/topics/abc123").text
    assert "Junta tudo numa lição" in html
    assert "Ok, juntei." in html
