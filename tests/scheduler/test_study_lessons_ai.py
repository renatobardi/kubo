"""KUBO-168 — Scheduler de Estudos: geração de lição com IA.

Testes UNIT com store e tutor mockados. O scheduler cria o registro de lição
(vazio) e depois chama o Tutor para preencher com IA (concept, scenario,
application, quiz, provenance). Se o Tutor falha, a lição fica como placeholder
(vazia) — o dono vê a lição agendada, e o próximo ciclo pode re-tentar.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest
from surrealdb import RecordID

from kubo.store.study import MaterialSection, PlanEntry, StudyPlan, Topic

_TENANT = RecordID("tenant", "t1")
_USER = RecordID("user", "u1")
_TOPIC_ID = RecordID("topic", "topic1")
_PLAN_ID = RecordID("study_plan", "plan1")
_ENTRY_ID = RecordID("plan_entry", "e1")
_LESSON_ID = RecordID("lesson", "l1")
_MATERIAL_ID = RecordID("material", "m1")
_CHAPTER_ID = RecordID("material_chapter", "c1")
_SECTION_ID = RecordID("material_section", "s1")
_SECTION = MaterialSection(
    id=_SECTION_ID,
    material=_MATERIAL_ID,
    material_chapter=_CHAPTER_ID,
    seq=1,
    title="Seção 1",
    anchor_text="",
    content="Conteúdo da seção",
    summary="Sumário",
    chapter_seq=1,
)


def _topic(state: str = "running") -> Topic:
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
        status="running",
        weekdays=["mon", "wed"],
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
                sections=[RecordID("material_section", f"s{i}")],
                created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            )
        )
    return out


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock()


# --- execute_study_lesson_job com IA ----------------------------------------------------


def test_lesson_job_fills_lesson_with_ai(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job de lição: cria registro vazio → chama Tutor → fill_lesson com conteúdo."""
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
        lambda db, **kw: (_plan(), _entries(1)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 0)
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: None
    )
    monkeypatch.setattr(study_lessons.study_store, "create_lesson", lambda db, **kw: _LESSON_ID)

    # Tutor mockado: devolve uma lição preenchida.
    from kubo.study.tutor import LessonOutput, ProvenanceItem, QuizItem

    lesson_output = LessonOutput(
        concept="Conceito",
        scenario="Cenário",
        application="Aplicação",
        recap=None,
        provenance=[ProvenanceItem(chapter_seq=1, section_seq=1, quote="trecho")],
        quiz=[
            QuizItem(question="Q1?", options=["A", "B"], explanation="E1", answer_index=0),
            QuizItem(question="Q2?", options=["C", "D"], explanation="E2", answer_index=1),
        ],
    )

    filled: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "fill_lesson",
        lambda db, **kw: filled.append(kw["lesson_id"]),
    )

    # Mocka a construção do Tutor, busca de seções e work_context.
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_sections_for_entry",
        lambda db, **kw: [_SECTION],
    )
    monkeypatch.setattr(
        study_lessons,
        "_build_tutor",
        lambda db, tenant_id, user_id: _FakeTutor(lesson_output),
    )
    monkeypatch.setattr(study_lessons, "_work_context_for", lambda db, user_id: "")

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    assert len(filled) == 1
    assert filled[0] == _LESSON_ID


def test_lesson_job_skips_fill_when_tutor_fails(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tutor devolve None → lição fica vazia (placeholder), não chama fill_lesson."""
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
        lambda db, **kw: (_plan(), _entries(1)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 0)
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: None
    )
    monkeypatch.setattr(study_lessons.study_store, "create_lesson", lambda db, **kw: _LESSON_ID)

    filled: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "fill_lesson",
        lambda db, **kw: filled.append(kw["lesson_id"]),
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_sections_for_entry",
        lambda db, **kw: [_SECTION],
    )
    monkeypatch.setattr(
        study_lessons,
        "_build_tutor",
        lambda db, tenant_id, user_id: _FakeTutor(None),
    )
    monkeypatch.setattr(study_lessons, "_work_context_for", lambda db, user_id: "")

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    assert filled == []


def test_lesson_job_retries_placeholder_lesson(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lição placeholder (sem concept) é re-tentada: não cria nova, reutiliza a existente."""
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
        lambda db, **kw: (_plan(), _entries(1)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 0)
    # Placeholder existe (Tutor falhou antes).
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: _LESSON_ID
    )
    created: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "create_lesson",
        lambda db, **kw: created.append(kw["plan_entry_id"]),
    )

    from kubo.study.tutor import LessonOutput, ProvenanceItem, QuizItem

    lesson_output = LessonOutput(
        concept="Conceito retry",
        scenario="Cenário",
        application="Aplicação",
        recap=None,
        provenance=[ProvenanceItem(chapter_seq=1, section_seq=1, quote="trecho")],
        quiz=[
            QuizItem(question="Q1?", options=["A", "B"], explanation="E1", answer_index=0),
            QuizItem(question="Q2?", options=["C", "D"], explanation="E2", answer_index=1),
        ],
    )
    filled: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "fill_lesson",
        lambda db, **kw: filled.append(kw["lesson_id"]),
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_sections_for_entry",
        lambda db, **kw: [_SECTION],
    )
    monkeypatch.setattr(
        study_lessons,
        "_build_tutor",
        lambda db, tenant_id, user_id: _FakeTutor(lesson_output),
    )
    monkeypatch.setattr(study_lessons, "_work_context_for", lambda db, user_id: "")

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    # Não criou nova lição (reutilizou a placeholder).
    assert created == []
    # Preencheu a lição existente.
    assert filled == [_LESSON_ID]


class _FakeTutor:
    """Fake de Tutor: devolve `output` (LessonOutput ou None)."""

    def __init__(self, output: object | None) -> None:
        self._output = output
        self.calls: list[dict[str, object]] = []

    def generate(self, **kw: object) -> object | None:
        self.calls.append(kw)
        return self._output


# --- execute_study_transition_job com IA (KUBO-168) --------------------------------------


def test_transition_job_generates_lesson_content(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transição scheduled→running cria lição e chama Tutor + fill_lesson."""
    from kubo.scheduler import study_lessons

    today = date(2026, 8, 3)  # segunda, véspera da terça (1º dia de cadência)
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
    monkeypatch.setattr(
        study_lessons.study_store,
        "transition_to_running",
        lambda db, **kw: _LESSON_ID,
    )

    from kubo.study.tutor import LessonOutput, ProvenanceItem, QuizItem

    lesson_output = LessonOutput(
        concept="Conceito transição",
        scenario="Cenário",
        application="Aplicação",
        recap=None,
        provenance=[ProvenanceItem(chapter_seq=1, section_seq=1, quote="trecho")],
        quiz=[
            QuizItem(question="Q1?", options=["A", "B"], explanation="E1", answer_index=0),
            QuizItem(question="Q2?", options=["C", "D"], explanation="E2", answer_index=1),
        ],
    )
    filled: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "fill_lesson",
        lambda db, **kw: filled.append(kw["lesson_id"]),
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_sections_for_entry",
        lambda db, **kw: [_SECTION],
    )
    monkeypatch.setattr(
        study_lessons,
        "_build_tutor",
        lambda db, tenant_id, user_id: _FakeTutor(lesson_output),
    )
    monkeypatch.setattr(study_lessons, "_work_context_for", lambda db, user_id: "")

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert filled == [_LESSON_ID]


def test_transition_job_skips_fill_when_no_lesson_returned(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """transition_to_running devolve None (idempotente) → não chama fill_lesson."""
    from kubo.scheduler import study_lessons

    today = date(2026, 8, 3)
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
    monkeypatch.setattr(
        study_lessons.study_store,
        "transition_to_running",
        lambda db, **kw: None,  # já transicionado (idempotente)
    )
    filled: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "fill_lesson",
        lambda db, **kw: filled.append(kw["lesson_id"]),
    )
    monkeypatch.setattr(
        study_lessons,
        "_build_tutor",
        lambda db, tenant_id, user_id: _FakeTutor(None),
    )

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert filled == []


def test_transition_job_skips_fill_when_sections_empty(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_sections_for_entry devolve [] → lição fica como placeholder."""
    from kubo.scheduler import study_lessons

    today = date(2026, 8, 3)
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
    monkeypatch.setattr(
        study_lessons.study_store,
        "transition_to_running",
        lambda db, **kw: _LESSON_ID,
    )
    filled: list[RecordID] = []
    monkeypatch.setattr(
        study_lessons.study_store,
        "fill_lesson",
        lambda db, **kw: filled.append(kw["lesson_id"]),
    )
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_sections_for_entry",
        lambda db, **kw: [],  # sem seções
    )
    monkeypatch.setattr(
        study_lessons,
        "_build_tutor",
        lambda db, tenant_id, user_id: _FakeTutor(None),
    )
    monkeypatch.setattr(study_lessons, "_work_context_for", lambda db, user_id: "")

    study_lessons.execute_study_transition_job(
        mock_db, tenant_id=_TENANT, user_id=_USER, today=today
    )
    assert filled == []  # placeholder, não preencheu


def test_lesson_job_passes_recent_misses_to_tutor(
    mock_db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O Tutor recebe os erros recentes do plano para recapitular (ADR-0049 §II)."""
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
        lambda db, **kw: (_plan(), _entries(1)),
    )
    monkeypatch.setattr(study_lessons.study_store, "count_lessons_for_plan", lambda db, **kw: 0)
    monkeypatch.setattr(
        study_lessons.study_store, "get_pending_lesson_for_entry", lambda db, **kw: None
    )
    monkeypatch.setattr(study_lessons.study_store, "create_lesson", lambda db, **kw: _LESSON_ID)
    monkeypatch.setattr(
        study_lessons.study_store,
        "get_sections_for_entry",
        lambda db, **kw: [_SECTION],
    )
    misses = ["Erro recente"]
    monkeypatch.setattr(
        study_lessons.study_store,
        "recent_misses_for_plan",
        lambda db, **kw: misses,
    )

    from kubo.study.tutor import LessonOutput, ProvenanceItem, QuizItem

    lesson_output = LessonOutput(
        concept="Conceito",
        scenario="Cenário",
        application="Aplicação",
        recap=None,
        provenance=[ProvenanceItem(chapter_seq=1, section_seq=1, quote="trecho")],
        quiz=[
            QuizItem(question="Q1?", options=["A", "B"], explanation="E1", answer_index=0),
            QuizItem(question="Q2?", options=["C", "D"], explanation="E2", answer_index=1),
        ],
    )
    fake_tutor = _FakeTutor(lesson_output)
    monkeypatch.setattr(
        study_lessons,
        "_build_tutor",
        lambda db, tenant_id, user_id: fake_tutor,
    )
    monkeypatch.setattr(study_lessons.study_store, "fill_lesson", lambda db, **kw: None)
    monkeypatch.setattr(study_lessons, "_work_context_for", lambda db, user_id: "")

    study_lessons.execute_study_lesson_job(mock_db, tenant_id=_TENANT, user_id=_USER, today=today)
    assert len(fake_tutor.calls) == 1
    assert fake_tutor.calls[0]["misses"] == misses
