"""KUBO-201 — Tela de Lição e envio de quiz.

Testes UNIT com a store stubada. O domínio de correção do quiz é testado em
`tests/study/test_quiz.py`; aqui validamos que a rota expõe a lição, renderiza o
quiz e persiste o registro de estudo.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.study import Lesson, StudyLog, StudyPlan, Topic

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_TOPIC_ID = RecordID("topic", "abc123")
_PLAN_ID = RecordID("study_plan", "plan1")
_LESSON_ID = RecordID("lesson", "lesson1")


def _topic(state: str = "running") -> Topic:
    return Topic(
        id=_TOPIC_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        title="Estudo",
        state=state,
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


def _plan() -> StudyPlan:
    return StudyPlan(
        id=_PLAN_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC_ID,
        status="running",
        weekdays=["mon", "wed"],
        target_date=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        activated_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


def _lesson(*, placeholder: bool = False) -> Lesson:
    return Lesson(
        id=_LESSON_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        study_plan=_PLAN_ID,
        plan_entry=RecordID("plan_entry", "e1"),
        scheduled_for=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        concept="Conceito" if not placeholder else "",
        scenario="Cenário" if not placeholder else "",
        application="Aplicação" if not placeholder else "",
        recap=None,
        quiz=[
            {
                "question": "Q1?",
                "options": ["A", "B"],
                "answer_index": 0,
                "explanation": "E1",
            },
            {
                "question": "Q2?",
                "options": ["C", "D"],
                "answer_index": 1,
                "explanation": "E2",
            },
        ]
        if not placeholder
        else [],
        provenance=[{"chapter_seq": 1, "section_seq": 1, "quote": "trecho"}]
        if not placeholder
        else [],
        is_placeholder=placeholder,
    )


@pytest.fixture(autouse=True)
def stub_lesson_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desacopla as rotas de lição da store real."""
    from kubo.api.routes import study as study_routes
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr(study_routes.client, "connect", _fake_connect)
    monkeypatch.setattr(study_routes.client, "connect_rw", _fake_connect)
    monkeypatch.setattr(study_routes.study_store, "get_topic", lambda db, **kw: _topic())
    monkeypatch.setattr(
        study_routes.study_store, "get_plan_for_topic", lambda db, **kw: (_plan(), [])
    )
    monkeypatch.setattr(study_routes.study_store, "get_lesson", lambda db, **kw: _lesson())
    monkeypatch.setattr(study_routes.study_store, "get_study_log", lambda db, **kw: None)
    monkeypatch.setattr(study_routes.study_store, "list_lessons_for_plan", lambda db, **kw: [])
    monkeypatch.setattr(study_routes.study_store, "list_study_logs_for_plan", lambda db, **kw: {})
    monkeypatch.setattr(study_routes.study_store, "list_materials_by_topic", lambda db, **kw: [])
    monkeypatch.setattr(study_routes.study_store, "list_chat_messages", lambda db, **kw: [])
    monkeypatch.setattr(study_routes, "_collect_all_sections", lambda db, ctx, topic_id: [])
    monkeypatch.setattr(
        study_routes.study_store,
        "create_study_log",
        lambda db, **kw: StudyLog(
            id=RecordID("study_log", "sl1"),
            tenant_id=_TENANT,
            user_id=_USER,
            lesson=_LESSON_ID,
            answers=list(kw.get("answers", [])),
            correct_count=kw.get("correct_count", 0),
            reaction=kw.get("reaction"),
            completed_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        ),
    )


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF da tela do tema."""
    html = authed_client.get(f"/study/topics/{_TOPIC_ID.id}").text
    import re

    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente"
    return m.group(1)


def test_lesson_page_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert (
        client.get(
            f"/study/topics/{_TOPIC_ID.id}/lessons/{_LESSON_ID.id}", follow_redirects=False
        ).status_code
        == 303
    )


def test_lesson_page_shows_content_and_quiz(authed_client: TestClient) -> None:
    """A lição renderiza os 4 blocos e as questões."""
    resp = authed_client.get(f"/study/topics/{_TOPIC_ID.id}/lessons/{_LESSON_ID.id}")
    body = resp.text

    assert resp.status_code == 200
    assert "Conceito" in body
    assert "Cenário" in body
    assert "Aplicação" in body
    assert "Q1?" in body
    assert "Q2?" in body


def test_lesson_page_404_for_missing_lesson(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lição inexistente (ou de outro usuário) é 404."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_lesson", lambda db, **kw: None)
    resp = authed_client.get(f"/study/topics/{_TOPIC_ID.id}/lessons/{_LESSON_ID.id}")
    assert resp.status_code == 404


def test_lesson_page_shows_placeholder(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Placeholder não mostra quiz e avisa que a lição está sendo gerada."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_lesson",
        lambda db, **kw: _lesson(placeholder=True),
    )
    resp = authed_client.get(f"/study/topics/{_TOPIC_ID.id}/lessons/{_LESSON_ID.id}")
    body = resp.text

    assert resp.status_code == 200
    assert "Q1?" not in body
    assert "sendo gerada pelo scheduler" in body.lower()


def test_submit_quiz_requires_csrf(authed_client: TestClient) -> None:
    """POST sem CSRF é 403."""
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/lessons/{_LESSON_ID.id}/submit",
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_submit_quiz_creates_study_log_and_redirects(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Envio correto cria o registro de estudo e redireciona pro tema."""
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_study_log",
        lambda db, **kw: (
            calls.append(kw)
            or StudyLog(
                id=RecordID("study_log", "sl1"),
                tenant_id=_TENANT,
                user_id=_USER,
                lesson=_LESSON_ID,
                answers=list(kw.get("answers", [])),
                correct_count=kw.get("correct_count", 0),
                reaction=kw.get("reaction"),
                completed_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
            )
        ),
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/lessons/{_LESSON_ID.id}/submit",
        data={"csrf": csrf, "answers": ["0", "1"], "reaction": "ok"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert f"/study/topics/{_TOPIC_ID.id}" in resp.headers["location"]
    assert len(calls) == 1
    assert calls[0]["answers"] == [0, 1]
    assert calls[0]["correct_count"] == 2
    assert calls[0]["reaction"] == "ok"


def test_submit_quiz_partial_answers_rejected(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Responder menos questões que o quiz é 400."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_study_log", lambda db, **kw: None)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        f"/study/topics/{_TOPIC_ID.id}/lessons/{_LESSON_ID.id}/submit",
        data={"csrf": csrf, "answers": ["0"]},
        follow_redirects=False,
    )
    assert resp.status_code == 400


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
