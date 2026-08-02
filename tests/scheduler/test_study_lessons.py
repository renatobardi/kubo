"""KUBO-166 — Scheduler de Estudos: transição scheduled→running + geração de lição.

Testes UNIT com store mockada. O scheduler chama a store; aqui validamos que:
- Job de transição busca 'scheduled', checa véspera do 1º dia, transiciona + cria 1ª lição
- Job de lição busca 'running', gera próxima lição na véspera do próximo dia de cadência
- Não gera para 'scheduled' (regular) ou 'archived'
- Tolerante a downtime (transição dispara na véspera OU depois)
- Plano concluído não gera mais lições
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
_ENTRY_ID_2 = RecordID("plan_entry", "e2")


def _topic(state: str = "scheduled") -> Topic:
    return Topic(
        id=_TOPIC_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        title="Estudo",
        state=state,
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


def _plan(activated_at: datetime | None = None, weekdays: list[str] | None = None) -> StudyPlan:
    return StudyPlan(
        id=_PLAN_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC_ID,
        status="active",
        weekdays=["mon", "wed"] if weekdays is None else weekdays,
        target_date=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        activated_at=activated_at or datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


def _entries(n: int = 1) -> list[PlanEntry]:
    out: list[PlanEntry] = []
    for i in range(1, n + 1):
        out.append(
            PlanEntry(
                id=RecordID("plan_entry", f"e{i}"),
                study_plan=_PLAN_ID,
                tenant_id=_TENANT,
                user_id=_USER,
                seq=i,
                title=f"Lição {i}",
                chapters=[RecordID("material_chapter", f"c{i}")],
                created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            )
        )
    return out


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock()


# --- execute_study_transition_job -------------------------------------------------------


def test_transition_job_transitions_scheduled_to_running(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de transição: scheduled + véspera do 1º dia → running + 1ª lição (atômico)."""
    from kubo.scheduler import study_lessons

    # 1º dia de cadência = 2026-08-03 (segunda). Véspera = 2026-08-02 (domingo).
    # activated_at = 2026-08-01 (sábado). next_study_day(after=2026-08-01, mon/wed) = 2026-08-03.
    # Hoje = 2026-08-02 = véspera → transiciona.
    today = date(2026, 8, 2)

    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="scheduled")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries(1)),
    )
    transitions: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "transition_to_running",
        lambda db, **kw: transitions.append(kw["topic_id"]),
    )

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert len(transitions) == 1


def test_transition_job_skips_when_before_eve(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de transição: scheduled mas ANTES da véspera → não transiciona."""
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
        lambda db, **kw: (_plan(), _entries(1)),
    )
    transitions: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "transition_to_running",
        lambda db, **kw: transitions.append(kw["topic_id"]),
    )

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert transitions == []


def test_transition_job_tolerates_downtime(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de transição: scheduled + DEPOIS da véspera (downtime) → transiciona."""
    from kubo.scheduler import study_lessons

    # Hoje = 2026-08-05 (quarta) — véspera foi 02/08, mas job não rodou.
    today = date(2026, 8, 5)
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="scheduled")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries(1)),
    )
    transitions: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "transition_to_running",
        lambda db, **kw: transitions.append(kw["topic_id"]),
    )

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert len(transitions) == 1


def test_transition_job_skips_no_cadence(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de transição: plano sem weekdays → não transiciona (log warning)."""
    from kubo.scheduler import study_lessons

    today = date(2026, 8, 2)
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="scheduled")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(weekdays=[]), _entries(1)),
    )
    transitions: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "transition_to_running",
        lambda db, **kw: transitions.append(kw["topic_id"]),
    )

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert transitions == []


def test_transition_job_only_queries_scheduled(
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


def test_lesson_job_generates_next_lesson_on_eve(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição: running + véspera do próximo dia de cadência → gera próxima lição.

    Cadência mon/wed. Hoje = 2026-08-04 (terça) = véspera de quarta (2026-08-05).
    1 lição já gerada (done=1), 2 entries → gera entry[1] (Lição 2).
    """
    from kubo.scheduler import study_lessons

    today = date(2026, 8, 4)  # terça, véspera de quarta
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="running")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries(2)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 1)
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: None
    )
    created: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "create_lesson",
        lambda db, **kw: created.append(kw["plan_entry_id"]),
    )

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    assert len(created) == 1
    assert created[0] == _ENTRY_ID_2  # próxima entry


def test_lesson_job_skips_when_before_eve(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição: running mas ANTES da véspera do próximo dia → não gera."""
    from kubo.scheduler import study_lessons

    # Cadência mon/wed. Hoje = 2026-08-06 (quinta).
    # Último dia de cadência = quarta 05/08. Próximo = segunda 10/08, véspera = domingo 09/08.
    # 06/08 < 09/08 → skip.
    today = date(2026, 8, 6)
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="running")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries(2)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 1)
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: None
    )
    created: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "create_lesson",
        lambda db, **kw: created.append(kw["plan_entry_id"]),
    )

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    assert created == []


def test_lesson_job_generates_on_study_day_downtime(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição: running + no próprio dia (downtime) → gera."""
    from kubo.scheduler import study_lessons

    # Hoje = 2026-08-05 (quarta) = dia de cadência. Véspera = 04/08 (terça).
    # Scheduler não rodou na véspera → gera no dia (downtime recovery).
    today = date(2026, 8, 5)
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="running")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries(2)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 1)
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: None
    )
    created: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "create_lesson",
        lambda db, **kw: created.append(kw["plan_entry_id"]),
    )

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    assert len(created) == 1


def test_lesson_job_skips_when_plan_complete(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição: running mas todas as lições já geradas → não gera."""
    from kubo.scheduler import study_lessons

    today = date(2026, 8, 4)
    monkeypatch.setattr(
        study_lessons.study_store,
        "list_topics_by_state",
        lambda db, **kw: [_topic(state="running")],
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_plan_for_topic",
        lambda db, **kw: (_plan(), _entries(2)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 2)
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: None
    )
    created: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "create_lesson",
        lambda db, **kw: created.append(kw["plan_entry_id"]),
    )

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    assert created == []


def test_lesson_job_only_queries_running(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição NÃO busca 'scheduled' ou 'archived' (filtra só running)."""
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
    assert calls == ["running"]
