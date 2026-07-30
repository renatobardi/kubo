"""Store de Lição e Registro de estudo (ADR-0043, KUBO-137), user-scoped no tenant.

Integração (SurrealDB real), banco próprio `test_lesson`. Três eixos que só o banco
prova: IDEMPOTÊNCIA do job da véspera (UNIQUE plano+dia), UNICIDADE do registro de
estudo (UNIQUE por lição) e ISOLAMENTO entre USUÁRIOS do mesmo tenant — lição é dado
pessoal, e um corte só por tenant deixaria passar a lição do colega.

O avanço do plano é comportamento, não detalhe: `next_unlessoned_entry` segue o `seq`
aprovado pelo dono e esgota no fim — a ordem do plano NUNCA muda por desempenho.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import MembershipRequiredError, StoreError
from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    PlanEntryInput,
    activate_plan,
    complete_lesson,
    count_lessons,
    create_lesson,
    create_material,
    create_topic,
    get_lesson,
    get_lesson_for_day,
    get_log_for_lesson,
    list_active_plans,
    list_lessons,
    next_unlessoned_entry,
    recent_misses,
    save_plan_proposal,
)
from kubo.study.parsing import ParsedChapter
from kubo.study.tutor import LessonOutput, QuizItem

pytestmark = pytest.mark.integration

_LESSON_DB = "test_lesson"

_WEEK = ["mon", "tue", "wed", "thu", "fri"]
_TARGET = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
# Dias de estudo consecutivos (meia-noite local do dono já convertida a UTC).
_DAY_1 = datetime(2026, 8, 6, 3, 0, tzinfo=timezone.utc)
_DAY_2 = datetime(2026, 8, 7, 3, 0, tzinfo=timezone.utc)
_DAY_3 = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_LESSON_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_LESSON_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_LESSON_DB};")


def _output(prefix: str = "Aula 1", *, recap: str | None = None) -> LessonOutput:
    """Saída da persona tutor: blocos + duas questões cuja resposta certa é o índice 0."""
    return LessonOutput(
        concept=f"{prefix}: o conceito.",
        scenario=f"{prefix}: o cenário.",
        application=f"{prefix}: a aplicação.",
        recap=recap,
        quiz=[
            QuizItem(
                question=f"{prefix} Q{i}?",
                options=["Certa", "Errada", "Também errada"],
                answer_index=0,
                explanation=f"{prefix}: porque sim {i}.",
            )
            for i in (1, 2)
        ],
    )


def _active_plan(db: Any, tenant_id: RecordID, user_id: RecordID, *, entries: int = 3) -> Any:
    """Material + tema + plano ATIVO com `entries` lições — o cenário de toda cena."""
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        title="Manual de Kubo",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/materials/t/u/m.epub",
        size_bytes=1024,
        chapters=[
            ParsedChapter(seq=i, title=f"Capítulo {i}", content=f"Conteúdo {i}.", part=None)
            for i in range(1, entries + 1)
        ],
    )
    topic = create_topic(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material.id, title=material.title
    )
    chapter_ids = [
        row["id"]
        for row in db.query(
            "SELECT * FROM material_chapter WHERE material = $m ORDER BY seq;",
            {"m": material.id},
        )
    ]
    plan = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        weekdays=_WEEK,
        target_date=_TARGET,
        entries=[
            PlanEntryInput(seq=i + 1, title=f"Aula {i + 1}", chapter_ids=[chapter_ids[i]])
            for i in range(entries)
        ],
    )
    return activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)


def _first_entry(db: Any, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID) -> Any:
    entry = next_unlessoned_entry(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    assert entry is not None
    return entry


def _lesson_on(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    plan: Any,
    day: datetime,
    *,
    prefix: str = "Aula 1",
) -> Any:
    """Gera a lição da PRÓXIMA entrada pendente do plano, marcada para `day`."""
    entry = _first_entry(db, tenant_id, user_id, plan.id)
    return create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        entry_id=entry.id,
        scheduled_for=day,
        output=_output(prefix),
    )


def _other_member(db: Any, tenant_id: RecordID) -> RecordID:
    """Segundo usuário, MEMBRO DO MESMO TENANT — o vizinho que não pode ver a lição."""
    other = tenancy.create_user(db, firebase_uid="other-user-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")
    return other.id


def test_list_active_plans_ignores_plans_still_proposed(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Só plano ATIVO produz lição: enquanto proposto, o job da véspera não o vê."""
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        title="Manual",
        fmt="epub",
        original_filename="m.epub",
        file_path="/data/m.epub",
        size_bytes=10,
        chapters=[ParsedChapter(seq=1, title="Cap 1", content="c", part=None)],
    )
    topic = create_topic(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material.id, title="Manual"
    )
    proposed = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        weekdays=_WEEK,
        target_date=_TARGET,
        entries=[PlanEntryInput(seq=1, title="Aula 1", chapter_ids=[])],
    )

    assert list_active_plans(db, tenant_id=tenant_id, user_id=user_id) == []

    activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=proposed.id)

    assert [p.id for p in list_active_plans(db, tenant_id=tenant_id, user_id=user_id)] == [
        proposed.id
    ]


def test_create_lesson_persists_the_blocks_and_the_quiz(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A lição nasce com os quatro blocos, o quiz congelado e o dia de estudo."""
    plan = _active_plan(db, tenant_id, user_id)
    entry = _first_entry(db, tenant_id, user_id, plan.id)

    lesson = create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        entry_id=entry.id,
        scheduled_for=_DAY_1,
        output=_output("Aula 1", recap="Você errou filas."),
    )

    assert lesson.study_plan == plan.id
    assert lesson.plan_entry == entry.id
    assert lesson.scheduled_for == _DAY_1
    assert lesson.recap == "Você errou filas."
    assert lesson.concept == "Aula 1: o conceito."
    assert [q["question"] for q in lesson.quiz] == ["Aula 1 Q1?", "Aula 1 Q2?"]
    fetched = get_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson.id)
    assert fetched is not None
    assert fetched.application == "Aula 1: a aplicação."


def test_get_lesson_for_day_finds_only_that_day(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O job pergunta pelo DIA: a lição de amanhã existe, a de depois ainda não."""
    plan = _active_plan(db, tenant_id, user_id)
    _lesson_on(db, tenant_id, user_id, plan, _DAY_1)

    scope = {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan.id}
    assert get_lesson_for_day(db, scheduled_for=_DAY_1, **scope) is not None
    assert get_lesson_for_day(db, scheduled_for=_DAY_2, **scope) is None


def test_second_lesson_for_the_same_day_is_refused(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """(plano, dia) é UNIQUE — é o que torna a véspera com várias janelas idempotente."""
    plan = _active_plan(db, tenant_id, user_id)
    _lesson_on(db, tenant_id, user_id, plan, _DAY_1)
    entry = _first_entry(db, tenant_id, user_id, plan.id)

    with pytest.raises(StoreError):
        create_lesson(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            plan_id=plan.id,
            entry_id=entry.id,
            scheduled_for=_DAY_1,
            output=_output("Aula 2"),
        )

    assert count_lessons(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id) == 1


def test_list_lessons_pages_the_history_most_recent_first(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Histórico paginado, lição mais recente primeiro — a de hoje abre a lista."""
    plan = _active_plan(db, tenant_id, user_id)
    _lesson_on(db, tenant_id, user_id, plan, _DAY_1, prefix="Aula 1")
    _lesson_on(db, tenant_id, user_id, plan, _DAY_2, prefix="Aula 2")
    _lesson_on(db, tenant_id, user_id, plan, _DAY_3, prefix="Aula 3")

    scope = {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan.id}
    page = list_lessons(db, limit=2, start=0, **scope)

    assert [lesson.scheduled_for for lesson in page] == [_DAY_3, _DAY_2]
    assert [lesson.scheduled_for for lesson in list_lessons(db, limit=2, start=2, **scope)] == [
        _DAY_1
    ]
    assert count_lessons(db, **scope) == 3


def test_next_unlessoned_entry_walks_the_plan_and_runs_out(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A próxima lição é sempre o menor `seq` sem lição — e o fim do plano é None.

    A ordem é a que o dono aprovou (ADR-0043): gerar uma lição só faz o ponteiro andar
    para a lição SEGUINTE, nunca reordena o que sobrou.
    """
    plan = _active_plan(db, tenant_id, user_id, entries=2)
    scope = {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan.id}

    first = next_unlessoned_entry(db, **scope)
    assert first is not None
    assert first.seq == 1

    _lesson_on(db, tenant_id, user_id, plan, _DAY_1)
    second = next_unlessoned_entry(db, **scope)
    assert second is not None
    assert second.seq == 2

    _lesson_on(db, tenant_id, user_id, plan, _DAY_2)
    assert next_unlessoned_entry(db, **scope) is None


def test_complete_lesson_records_the_answers(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Concluir grava respostas, acertos e reação, e a lição passa a ter registro."""
    plan = _active_plan(db, tenant_id, user_id)
    lesson = _lesson_on(db, tenant_id, user_id, plan, _DAY_1)

    log = complete_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson.id,
        answers=[0, 1],
        correct_count=1,
        reaction="dificil",
    )

    assert log.lesson == lesson.id
    assert log.answers == [0, 1]
    assert log.correct_count == 1
    assert log.reaction == "dificil"
    assert log.completed_at is not None
    fetched = get_log_for_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson.id)
    assert fetched is not None
    assert fetched.correct_count == 1


def test_lesson_without_log_has_no_record(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Lição pendente não tem registro — é o que a tela usa para mostrar o formulário."""
    plan = _active_plan(db, tenant_id, user_id)
    lesson = _lesson_on(db, tenant_id, user_id, plan, _DAY_1)

    assert get_log_for_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson.id) is None


def test_completing_the_same_lesson_twice_is_refused(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Um registro POR LIÇÃO: o segundo envio não reescreve o desempenho já registrado."""
    plan = _active_plan(db, tenant_id, user_id)
    lesson = _lesson_on(db, tenant_id, user_id, plan, _DAY_1)
    complete_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson.id,
        answers=[0, 1],
        correct_count=1,
        reaction="ok",
    )

    with pytest.raises(StoreError):
        complete_lesson(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            lesson_id=lesson.id,
            answers=[0, 0],
            correct_count=2,
            reaction="facil",
        )

    again = get_log_for_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson.id)
    assert again is not None
    assert again.correct_count == 1
    assert again.reaction == "ok"


def test_recent_misses_returns_only_the_wrong_questions_newest_first(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A recapitulação vem do que o dono ERROU, do registro mais recente para trás.

    Questão acertada não volta: recapitular o que já foi acertado gastaria a abertura
    da lição com o que não precisa de reforço.
    """
    plan = _active_plan(db, tenant_id, user_id)
    first = _lesson_on(db, tenant_id, user_id, plan, _DAY_1, prefix="Aula 1")
    complete_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=first.id,
        answers=[1, 0],  # errou a Q1, acertou a Q2
        correct_count=1,
        reaction="dificil",
    )
    second = _lesson_on(db, tenant_id, user_id, plan, _DAY_2, prefix="Aula 2")
    complete_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=second.id,
        answers=[0, 2],  # acertou a Q1, errou a Q2
        correct_count=1,
        reaction="ok",
    )

    scope = {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan.id}
    misses = recent_misses(db, **scope)

    assert misses == ["Aula 2 Q2?", "Aula 1 Q1?"]
    assert recent_misses(db, limit=1, **scope) == ["Aula 2 Q2?"]


def test_lesson_with_everything_right_leaves_no_misses(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Sem erro não há recapitulação — a lição seguinte abre direto no conceito."""
    plan = _active_plan(db, tenant_id, user_id)
    lesson = _lesson_on(db, tenant_id, user_id, plan, _DAY_1)
    complete_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson.id,
        answers=[0, 0],
        correct_count=2,
        reaction="facil",
    )

    assert recent_misses(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id) == []


def test_lesson_is_invisible_to_another_member_of_same_tenant(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O corte é por USUÁRIO: o vizinho de tenant não lê nem conclui a lição alheia."""
    plan = _active_plan(db, tenant_id, user_id)
    lesson = _lesson_on(db, tenant_id, user_id, plan, _DAY_1)
    other_id = _other_member(db, tenant_id)
    theirs = {"tenant_id": tenant_id, "user_id": other_id}

    assert list_active_plans(db, **theirs) == []
    assert get_lesson(db, lesson_id=lesson.id, **theirs) is None
    assert get_lesson_for_day(db, plan_id=plan.id, scheduled_for=_DAY_1, **theirs) is None
    assert list_lessons(db, plan_id=plan.id, limit=10, start=0, **theirs) == []
    assert next_unlessoned_entry(db, plan_id=plan.id, **theirs) is None
    with pytest.raises(StoreError):
        complete_lesson(
            db, lesson_id=lesson.id, answers=[0, 0], correct_count=2, reaction=None, **theirs
        )

    assert get_log_for_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson.id) is None


def test_lesson_store_requires_membership(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Contrato KUBO-123: sem membership no tenant, nem leitura acontece."""
    plan = _active_plan(db, tenant_id, user_id)
    stranger = tenancy.create_user(db, firebase_uid="stranger-uid").id

    with pytest.raises(MembershipRequiredError):
        list_active_plans(db, tenant_id=tenant_id, user_id=stranger)
    with pytest.raises(MembershipRequiredError):
        next_unlessoned_entry(db, tenant_id=tenant_id, user_id=stranger, plan_id=plan.id)
