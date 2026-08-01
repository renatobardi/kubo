"""Ciclo de vida do plano (ADR-0043, KUBO-138): pausar, retomar, completar.

Integração (SurrealDB real), banco próprio `test_plan_lifecycle`. Três eixos que só o
banco prova: as TRANSIÇÕES válidas (e a recusa das inválidas), o ACUMULADOR de dias
pausados sobrevivendo a duas pausas, e o ISOLAMENTO entre usuários do mesmo tenant —
plano é dado pessoal, e um corte só por tenant deixaria o vizinho pausar o plano alheio.

A contagem de conclusões e as datas de conclusão vivem aqui (e não no domínio) porque
são leitura de banco; o que se FAZ com elas é de `tests/study/test_progress.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    PlanEntryInput,
    activate_plan,
    complete_lesson,
    complete_plan,
    completion_dates,
    count_completed_lessons,
    create_lesson,
    create_material,
    create_topic,
    get_plan_for_topic,
    list_active_plans,
    next_unlessoned_entry,
    pause_plan,
    resume_plan,
    save_plan_proposal,
)
from kubo.study.parsing import ParsedChapter
from kubo.study.tutor import LessonOutput, ProvenanceItem, QuizItem

pytestmark = pytest.mark.integration

_LIFECYCLE_DB = "test_plan_lifecycle"

_WEEK = ["mon", "tue", "wed", "thu", "fri"]
_TARGET = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
_NEW_TARGET = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
# Dias de estudo (meia-noite local do dono já convertida a UTC).
_DAY_1 = datetime(2026, 8, 6, 3, 0, tzinfo=timezone.utc)
_DAY_2 = datetime(2026, 8, 7, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_LIFECYCLE_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_LIFECYCLE_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_LIFECYCLE_DB};")


def _output(prefix: str = "Aula 1") -> LessonOutput:
    """Saída da persona tutor: blocos + duas questões cuja resposta certa é o índice 0.

    DUAS questões porque `LessonOutput.quiz` exige `min_length=2` (KUBO-137) — um quiz
    de uma questão nem chega a ser construído. Mesmo molde de `tests/store/test_lesson.py`.
    """
    return LessonOutput(
        concept=f"{prefix}: o conceito.",
        scenario=f"{prefix}: o cenário.",
        application=f"{prefix}: a aplicação.",
        recap=None,
        provenance=[ProvenanceItem(chapter_seq=1, quote=f"{prefix}: trecho de origem.")],
        quiz=[
            QuizItem(
                question=f"{prefix} Q{i}?",
                options=["Certa", "Errada"],
                answer_index=0,
                explanation=f"{prefix}: porque sim {i}.",
            )
            for i in (1, 2)
        ],
    )


def _proposed_plan(db: Any, tenant_id: RecordID, user_id: RecordID, *, entries: int = 2) -> Any:
    """Material + tema + plano PROPOSTO com `entries` lições."""
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
    return save_plan_proposal(
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


def _active_plan(db: Any, tenant_id: RecordID, user_id: RecordID, *, entries: int = 2) -> Any:
    """O cenário de quase toda cena: plano já ativado."""
    plan = _proposed_plan(db, tenant_id, user_id, entries=entries)
    return activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)


def _study(db: Any, tenant_id: RecordID, user_id: RecordID, plan: Any, day: datetime) -> Any:
    """Gera a próxima lição pendente do plano em `day` e registra o estudo dela."""
    entry = next_unlessoned_entry(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert entry is not None
    lesson = create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        entry_id=entry.id,
        scheduled_for=day,
        output=_output(entry.title),
    )
    return complete_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson.id,
        answers=[0],
        correct_count=1,
        reaction="ok",
    )


def _other_member(db: Any, tenant_id: RecordID) -> RecordID:
    """Segundo usuário, MEMBRO DO MESMO TENANT — o vizinho que não pode tocar o plano."""
    other = tenancy.create_user(db, firebase_uid="other-user-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")
    return other.id


# --- Pausar --------------------------------------------------------------------------


def test_pause_marks_the_plan_paused_and_stamps_when(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Pausar sai de `active` e carimba `paused_at` — é o que congela a régua depois."""
    plan = _active_plan(db, tenant_id, user_id)

    paused = pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    assert paused.status == "paused"
    assert paused.paused_at is not None
    stored = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=plan.topic)
    assert stored is not None
    assert stored.status == "paused"
    assert stored.paused_at is not None


def test_paused_plan_is_not_active_anymore(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """O job da véspera só olha planos ATIVOS: pausado some da lista, logo não gera lição."""
    plan = _active_plan(db, tenant_id, user_id)
    pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    assert list_active_plans(db, tenant_id=tenant_id, user_id=user_id) == []


def test_pausing_a_proposed_plan_is_refused(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Plano ainda proposto não tem régua para congelar — recusa legível, não silêncio."""
    plan = _proposed_plan(db, tenant_id, user_id)

    with pytest.raises(StoreError):
        pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)


def test_pausing_twice_is_refused(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Duplo clique em "Pausar" não pode reiniciar o `paused_at` já valendo.

    Sobrescrever a marca perderia os dias congelados desde a primeira pausa.
    """
    plan = _active_plan(db, tenant_id, user_id)
    pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    with pytest.raises(StoreError):
        pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)


def test_another_member_cannot_pause_my_plan(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Plano do vizinho é INVISÍVEL, não "negado": pausar de fora falha e nada muda."""
    plan = _active_plan(db, tenant_id, user_id)
    intruder = _other_member(db, tenant_id)

    with pytest.raises(StoreError):
        pause_plan(db, tenant_id=tenant_id, user_id=intruder, plan_id=plan.id)

    mine = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=plan.topic)
    assert mine is not None
    assert mine.status == "active"


# --- Retomar -------------------------------------------------------------------------


def test_resume_reactivates_clears_the_mark_and_records_the_new_target(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Retomar volta a `active`, limpa `paused_at` e grava a data-alvo recalculada."""
    plan = _active_plan(db, tenant_id, user_id)
    pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    resumed = resume_plan(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        paused_days_add=3,
        new_target=_NEW_TARGET,
    )

    assert resumed.status == "active"
    assert resumed.paused_at is None
    assert resumed.target_date == _NEW_TARGET
    assert resumed.paused_days == 3


def test_paused_days_accumulate_across_pauses(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Duas pausas congelam a régua duas vezes: o acumulador SOMA, não substitui.

    Substituir faria a segunda pausa apagar a primeira — e o atraso da primeira parada
    reapareceria do nada semanas depois.
    """
    plan = _active_plan(db, tenant_id, user_id)
    scope = {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan.id}

    pause_plan(db, **scope)
    resume_plan(db, paused_days_add=3, new_target=_NEW_TARGET, **scope)
    pause_plan(db, **scope)
    resumed = resume_plan(db, paused_days_add=2, new_target=_NEW_TARGET, **scope)

    assert resumed.paused_days == 5
    stored = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=plan.topic)
    assert stored is not None
    assert stored.paused_days == 5


def test_resuming_an_active_plan_is_refused(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Retomar o que nunca parou somaria dias congelados que não existiram."""
    plan = _active_plan(db, tenant_id, user_id)

    with pytest.raises(StoreError):
        resume_plan(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            plan_id=plan.id,
            paused_days_add=3,
            new_target=_NEW_TARGET,
        )


def test_another_member_cannot_resume_my_plan(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Retomar de fora falha e o plano continua pausado."""
    plan = _active_plan(db, tenant_id, user_id)
    pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    intruder = _other_member(db, tenant_id)

    with pytest.raises(StoreError):
        resume_plan(
            db,
            tenant_id=tenant_id,
            user_id=intruder,
            plan_id=plan.id,
            paused_days_add=3,
            new_target=_NEW_TARGET,
        )

    mine = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=plan.topic)
    assert mine is not None
    assert mine.status == "paused"


# --- Completar -----------------------------------------------------------------------


def test_complete_closes_an_active_plan(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Plano completo sai de ativo: nada mais é gerado nem avisado para ele."""
    plan = _active_plan(db, tenant_id, user_id)

    completed = complete_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    assert completed.status == "completed"
    assert list_active_plans(db, tenant_id=tenant_id, user_id=user_id) == []


def test_completing_a_paused_plan_is_refused(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Plano pausado não fecha sozinho — quem pausou decide se volta ou desiste."""
    plan = _active_plan(db, tenant_id, user_id)
    pause_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    with pytest.raises(StoreError):
        complete_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)


def test_completing_twice_is_refused(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Fechar de novo mandaria uma segunda mensagem de parabéns pelo mesmo plano."""
    plan = _active_plan(db, tenant_id, user_id)
    complete_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    with pytest.raises(StoreError):
        complete_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)


# --- Conclusões (o `done` da régua) ---------------------------------------------------


def test_count_completed_lessons_counts_only_what_was_studied(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Lição GERADA não conta; conta lição ESTUDADA — a diferença é o atraso do dono."""
    plan = _active_plan(db, tenant_id, user_id)
    scope = {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan.id}

    assert count_completed_lessons(db, **scope) == 0

    entry = next_unlessoned_entry(db, **scope)
    assert entry is not None
    create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        entry_id=entry.id,
        scheduled_for=_DAY_1,
        output=_output(),
    )

    assert count_completed_lessons(db, **scope) == 0  # gerada, não estudada

    _study(db, tenant_id, user_id, plan, _DAY_2)

    assert count_completed_lessons(db, **scope) == 1


def test_completion_dates_come_back_in_order(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """As datas saem ordenadas: o streak é uma caminhada, e ordem é o contrato dela."""
    plan = _active_plan(db, tenant_id, user_id)
    first = _study(db, tenant_id, user_id, plan, _DAY_1)
    second = _study(db, tenant_id, user_id, plan, _DAY_2)

    dates = completion_dates(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    assert dates == sorted(dates)
    assert dates == [first.completed_at, second.completed_at]


def test_completions_of_another_member_are_invisible(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A régua do vizinho não entra na minha: contagem e datas ficam vazias para ele.

    Um corte só por tenant somaria o estudo do colega ao meu progresso — e o painel
    mostraria um avanço que não é meu.
    """
    plan = _active_plan(db, tenant_id, user_id)
    _study(db, tenant_id, user_id, plan, _DAY_1)
    intruder = _other_member(db, tenant_id)
    scope = {"tenant_id": tenant_id, "user_id": intruder, "plan_id": plan.id}

    assert count_completed_lessons(db, **scope) == 0
    assert completion_dates(db, **scope) == []
