"""Job da véspera (ADR-0043, KUBO-137): a lição do próximo dia de estudo.

Unit puro — sem SurrealDB e sem BlockingScheduler: o alvo é a FUNÇÃO
`generate_upcoming_lessons`, no molde dos testes de `execute_sweep_job` (fakes de db,
store e tutor). Nenhum teste toca LiteLLM: o tutor entra pela costura `tutor_factory`.

O relógio é EXPLÍCITO (`now` por parâmetro) e a timezone vem do env: sem os dois, o dia
que o job prepara mudaria conforme a hora em que a suíte roda — e o teste viraria
bomba-relógio.

As funções de store são trocadas na origem (`kubo.store.study.*`), não numa cópia
importada: a implementação as acessa como atributo do módulo (idioma de
`kubo/api/routes/study.py`), então o patch vale qualquer que seja o alias do import.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from surrealdb import RecordID

from kubo.store.study import Lesson, MaterialChapter, PlanEntry, StudyPlan
from kubo.study.tutor import LessonOutput, QuizItem

_TENANT = RecordID("tenant", "t1")
_USER = RecordID("user", "u1")
_TZ = "America/Sao_Paulo"
_WEEK = ["mon", "tue", "wed", "thu", "fri"]

# Quarta-feira, 17:00 em São Paulo — a véspera roda no fim do dia.
_NOW = datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)
# O próximo dia habitado depois de quarta é quinta; meia-noite na tz do dono.
_NEXT_DAY = datetime.combine(datetime(2026, 8, 6).date(), time.min, tzinfo=ZoneInfo(_TZ))

_WORK_CONTEXT = "Arquiteto de plataforma; times pequenos."


def _plan(key: str = "p1") -> StudyPlan:
    return StudyPlan(
        id=RecordID("study_plan", key),
        tenant_id=_TENANT,
        user_id=_USER,
        topic=RecordID("topic", key),
        status="active",
        weekdays=_WEEK,
        target_date=datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc),
        activated_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def _entry(plan: StudyPlan, seq: int = 1) -> PlanEntry:
    return PlanEntry(
        id=RecordID("plan_entry", f"{plan.id.id}-e{seq}"),
        study_plan=plan.id,
        seq=seq,
        title=f"Aula {seq}",
        chapters=[RecordID("material_chapter", f"c{seq}")],
    )


def _chapters() -> list[MaterialChapter]:
    return [
        MaterialChapter(
            id=RecordID("material_chapter", f"c{i}"),
            material=RecordID("material", "m1"),
            seq=i,
            title=f"Capítulo {i}",
            part=None,
            content=f"Conteúdo {i}.",
        )
        for i in (1, 2)
    ]


def _output() -> LessonOutput:
    return LessonOutput(
        concept="O conceito.",
        scenario="O cenário.",
        application="A aplicação.",
        recap=None,
        quiz=[
            QuizItem(
                question=f"Q{i}?",
                options=["a", "b"],
                answer_index=0,
                explanation="porque sim",
            )
            for i in (1, 2)
        ],
    )


def _lesson(plan: StudyPlan, entry: PlanEntry, day: datetime) -> Lesson:
    return Lesson(
        id=RecordID("lesson", f"{plan.id.id}-{day.date().isoformat()}"),
        tenant_id=_TENANT,
        user_id=_USER,
        study_plan=plan.id,
        plan_entry=entry.id,
        scheduled_for=day,
        concept="O conceito.",
        scenario="O cenário.",
        application="A aplicação.",
        recap=None,
        quiz=[],
        created_at=_NOW,
    )


class _FakeTutor:
    """Fake do `Tutor`: devolve as saídas na ordem dada (None = LLM caído) e registra as
    chamadas. A MESMA instância é devolvida pelo factory a cada uso, então o teste não
    depende de o job construir o tutor uma vez ou uma vez por plano."""

    def __init__(self, *outputs: LessonOutput | None) -> None:
        self._outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def generate(self, **kwargs: Any) -> LessonOutput | None:
        self.calls.append(kwargs)
        if not self._outputs:
            return _output()
        return self._outputs.pop(0)


class _Spy:
    """Espiã de função de store: registra os kwargs e devolve o resultado combinado."""

    def __init__(self, result: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result

    def __call__(self, db: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if callable(self.result):
            return self.result(kwargs)
        return self.result


@pytest.fixture(autouse=True)
def study_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timezone do dono fixa: o dia de estudo é o dia LOCAL, não o do container (UTC)."""
    monkeypatch.setenv("TZ", _TZ)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Spy]:
    """Store inteira em espiãs: nenhum plano ativo, nenhuma lição, nada gravado."""
    spies = {
        "list_active_plans": _Spy([]),
        "get_lesson_for_day": _Spy(None),
        "next_unlessoned_entry": _Spy(None),
        "recent_misses": _Spy([]),
        "list_chapters_by_ids": _Spy(_chapters()),
        "create_lesson": _Spy(),
    }
    for name, spy in spies.items():
        monkeypatch.setattr(f"kubo.store.study.{name}", spy)
    monkeypatch.setattr(
        "kubo.store.tenancy.get_user",
        lambda db, user_id: type("U", (), {"id": user_id, "work_context": _WORK_CONTEXT})(),
    )
    return spies


def _run(tutor: _FakeTutor, *, db: Any = None) -> list[RecordID]:
    from kubo.scheduler.study import generate_upcoming_lessons

    return generate_upcoming_lessons(
        db if db is not None else object(),
        tenant_id=_TENANT,
        user_id=_USER,
        now=_NOW,
        tutor_factory=lambda: tutor,
    )


def test_no_active_plan_generates_nothing(store: dict[str, _Spy]) -> None:
    """Sem plano ativo o job não chama o LLM nem grava — a véspera passa em branco."""
    tutor = _FakeTutor()

    assert _run(tutor) == []
    assert tutor.calls == []
    assert store["create_lesson"].calls == []


def test_generates_the_lesson_for_the_next_study_day(store: dict[str, _Spy]) -> None:
    """O dia da lição é o PRÓXIMO dia habilitado — quarta à noite prepara a quinta."""
    plan = _plan()
    entry = _entry(plan)
    store["list_active_plans"].result = [plan]
    store["next_unlessoned_entry"].result = entry
    created = _lesson(plan, entry, _NEXT_DAY)
    store["create_lesson"].result = created

    assert _run(_FakeTutor(_output())) == [created.id]

    call = store["create_lesson"].calls[0]
    assert call["plan_id"] == plan.id
    assert call["entry_id"] == entry.id
    assert call["scheduled_for"] == _NEXT_DAY
    assert call["tenant_id"] == _TENANT
    assert call["user_id"] == _USER


def test_existing_lesson_for_the_day_is_not_regenerated(store: dict[str, _Spy]) -> None:
    """Idempotência: a segunda janela de retry da mesma véspera não gera de novo.

    Sem isso, um cron com várias janelas gastaria uma chamada de LLM por janela e
    esbarraria no UNIQUE (plano, dia) — erro no log em vez de trabalho evitado.
    """
    plan = _plan()
    entry = _entry(plan)
    store["list_active_plans"].result = [plan]
    store["next_unlessoned_entry"].result = entry
    store["get_lesson_for_day"].result = _lesson(plan, entry, _NEXT_DAY)
    tutor = _FakeTutor(_output())

    assert _run(tutor) == []
    assert tutor.calls == []
    assert store["create_lesson"].calls == []


def test_plan_that_ran_out_of_entries_is_skipped(store: dict[str, _Spy]) -> None:
    """Plano no fim das lições não gera nada — completar plano é fatia 5, não erro aqui."""
    store["list_active_plans"].result = [_plan()]
    store["next_unlessoned_entry"].result = None
    tutor = _FakeTutor(_output())

    assert _run(tutor) == []
    assert tutor.calls == []
    assert store["create_lesson"].calls == []


def test_tutor_failure_does_not_create_a_lesson(store: dict[str, _Spy]) -> None:
    """LLM caído deixa o plano sem lição naquela janela — nada é gravado pela metade."""
    plan = _plan()
    store["list_active_plans"].result = [plan]
    store["next_unlessoned_entry"].result = _entry(plan)

    assert _run(_FakeTutor(None)) == []
    assert store["create_lesson"].calls == []


def test_a_failing_plan_does_not_block_the_others(store: dict[str, _Spy]) -> None:
    """Um plano sem lição não condena os demais: o job segue para o próximo.

    Sem isolamento, o primeiro plano com LLM caído deixaria todos os seguintes sem
    lição no dia — e o dono descobriria só na hora de estudar.
    """
    first, second = _plan("p1"), _plan("p2")
    store["list_active_plans"].result = [first, second]
    store["next_unlessoned_entry"].result = lambda kw: _entry(_plan(str(kw["plan_id"].id)))
    store["create_lesson"].result = lambda kw: _lesson(
        _plan(str(kw["plan_id"].id)), _entry(_plan(str(kw["plan_id"].id))), kw["scheduled_for"]
    )

    created = _run(_FakeTutor(None, _output()))

    assert len(created) == 1
    assert [call["plan_id"] for call in store["create_lesson"].calls] == [second.id]


def test_generates_one_lesson_per_active_plan(store: dict[str, _Spy]) -> None:
    """Dois planos ativos, duas lições — uma por plano, no mesmo dia-alvo."""
    first, second = _plan("p1"), _plan("p2")
    store["list_active_plans"].result = [first, second]
    store["next_unlessoned_entry"].result = lambda kw: _entry(_plan(str(kw["plan_id"].id)))
    store["create_lesson"].result = lambda kw: _lesson(
        _plan(str(kw["plan_id"].id)), _entry(_plan(str(kw["plan_id"].id))), kw["scheduled_for"]
    )

    created = _run(_FakeTutor(_output(), _output()))

    assert len(created) == 2
    assert [call["plan_id"] for call in store["create_lesson"].calls] == [first.id, second.id]


def test_tutor_receives_the_entry_title_the_owner_context_and_recent_misses(
    store: dict[str, _Spy],
) -> None:
    """A lição é adaptada: título da entrada, contexto de trabalho do dono e o que ele errou.

    Os erros recentes são o que vira recapitulação na abertura da lição seguinte — sem
    eles chegando ao tutor, a adaptação prometida pelo ADR-0043 não acontece.
    """
    plan = _plan()
    entry = _entry(plan, seq=3)
    store["list_active_plans"].result = [plan]
    store["next_unlessoned_entry"].result = entry
    store["recent_misses"].result = ["O que é backpressure?"]
    store["create_lesson"].result = _lesson(plan, entry, _NEXT_DAY)
    tutor = _FakeTutor(_output())

    _run(tutor)

    call = tutor.calls[0]
    assert call["entry_title"] == "Aula 3"
    assert call["work_context"] == _WORK_CONTEXT
    assert list(call["misses"]) == ["O que é backpressure?"]
    assert store["recent_misses"].calls[0]["plan_id"] == plan.id
