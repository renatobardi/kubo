"""KUBO-166 — Scheduler de Estudos: transição scheduled→running + geração de lição.

Testes UNIT com store mockada. O scheduler chama a store; aqui validamos que:
- Job de transição busca 'scheduled', checa véspera do 1º dia, transiciona + cria 1ª lição
- Job de lição busca 'running', gera próxima lição
- Não gera para 'scheduled' (regular) ou 'archived'
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest
from surrealdb import RecordID

from kubo.store.study import PlanEntry, StudyPlan, Topic

_TENANT = RecordID("tenant", "t1")
_USER = RecordID("user", "u1")
_TOPIC_ID = RecordID("topic", "topic1")
_PLAN_ID = RecordID("study_plan", "plan1")
_ENTRY_ID = RecordID("plan_entry", "e1")


def _topic(state: str = "scheduled") -> Topic:
    return Topic(
        id=_TOPIC_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        title="Estudo",
        state=state,
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


def _plan(activated_at: datetime | None = None) -> StudyPlan:
    return StudyPlan(
        id=_PLAN_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC_ID,
        status="active",
        weekdays=["mon", "wed"],
        target_date=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        activated_at=activated_at or datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


def _entries() -> list[PlanEntry]:
    return [
        PlanEntry(
            id=_ENTRY_ID,
            study_plan=_PLAN_ID,
            tenant_id=_TENANT,
            user_id=_USER,
            seq=1,
            title="Lição 1",
            chapters=[RecordID("material_chapter", "c1")],
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        ),
    ]


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock()


# --- execute_study_transition_job -------------------------------------------------------


def test_transition_job_transitions_scheduled_to_running(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de transição: scheduled + véspera do 1º dia → running + 1ª lição."""
    from kubo.scheduler import study_lessons

    # 1º dia de cadência = 2026-08-03 (segunda). Véspera = 2026-08-02 (domingo).
    # activated_at = 2026-08-01 (sábado). next_study_day(after=2026-08-01, mon/wed) = 2026-08-03.
    # Hoje = 2026-08-02 = véspera → transiciona.
    today = date(2026, 8, 2)

    # Mock: list_topics_by_state devolve 1 tema scheduled
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="scheduled")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries()),
    )
    transitions: list[RecordID] = []
    lessons: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "set_topic_state",
        lambda db, **kw: transitions.append(kw["topic_id"]),
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "create_lesson",
        lambda db, **kw: lessons.append(kw["plan_id"]),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 0)

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert len(transitions) == 1
    assert len(lessons) == 1


def test_transition_job_skips_when_not_eve(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de transição: scheduled mas NÃO é véspera → não transiciona."""
    from kubo.scheduler import study_lessons

    today = date(2026, 8, 1)  # sábado, 1º dia é 03/08 (segunda), véspera é 02/08
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="scheduled")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries()),
    )
    transitions: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "set_topic_state",
        lambda db, **kw: transitions.append(kw["topic_id"]),
    )
    monkeypatch.setattr(study_lessons.study_store, "create_lesson", lambda db, **kw: None)

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert transitions == []


def test_transition_job_does_not_process_running(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de transição só busca 'scheduled', não 'running'."""
    from kubo.scheduler import study_lessons

    calls: list[str] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: calls.append(kw["state"]) or [],
    )
    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=date(2026, 8, 2)
    )
    assert calls == ["scheduled"]


# --- execute_study_lesson_job ----------------------------------------------------------


def test_lesson_job_processes_running(mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Job de lição busca 'running' e gera próxima lição."""
    from kubo.scheduler import study_lessons

    calls: list[str] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: calls.append(kw["state"]) or [],
    )
    study_lessons.execute_study_lesson_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=date(2026, 8, 4)
    )
    assert "running" in calls


def test_lesson_job_does_not_process_scheduled(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição NÃO busca 'scheduled' (filtra só running)."""
    from kubo.scheduler import study_lessons

    calls: list[str] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: calls.append(kw["state"]) or [],
    )
    study_lessons.execute_study_lesson_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=date(2026, 8, 4)
    )
    assert "scheduled" not in calls


def test_lesson_job_does_not_process_archived(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição NÃO busca 'archived'."""
    from kubo.scheduler import study_lessons

    calls: list[str] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: calls.append(kw["state"]) or [],
    )
    study_lessons.execute_study_lesson_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=date(2026, 8, 4)
    )
    assert "archived" not in calls
