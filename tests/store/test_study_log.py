"""KUBO-201 — Store da Lição e do Registro de estudo (ADR-0049 §I/§II).

Integração (SurrealDB real). Fecha o ciclo que o épico KUBO-160 deixou aberto: a
lição passa a ser LEGÍVEL (a UI precisa dela) e o desempenho do dono passa a ser
GRAVADO (`study_log`), que é o que faz o progresso andar e alimenta a
recapitulação da lição seguinte.

Semântica de placeholder: `create_lesson` grava `concept = ''`. Uma lição sem
`concept` é placeholder — o Tutor falhou e o scheduler deve RE-TENTAR a mesma
entrada, não avançar. Os testes abaixo cravam isso porque as funções existentes
comparavam com `NONE` (e string vazia não é NONE), então placeholder contava
como concluída e a re-tentativa nunca achava nada.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import client, migrations
from kubo.store.study import (
    count_lessons_for_plan,
    create_lesson,
    create_material,
    create_study_log,
    create_topic,
    fill_lesson,
    get_lesson,
    get_pending_lesson_for_entry,
    get_study_log,
    get_topic_progress,
    list_all_sections,
    list_lessons_for_plan,
    list_study_logs_for_plan,
    recent_misses_for_plan,
    save_plan_proposal,
)
from kubo.study.parsing import ParsedChapter, SectionPart

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_log"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_STUDY_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_STUDY_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_STUDY_DB};")


def _quiz() -> list[dict[str, Any]]:
    return [
        {
            "question": "O que é um Tema?",
            "options": ["Container", "Arquivo"],
            "answer_index": 0,
            "explanation": "Tema é o container.",
        },
        {
            "question": "O que é uma Seção?",
            "options": ["Capítulo", "Divisão tópica"],
            "answer_index": 1,
            "explanation": "Seção é divisão tópica.",
        },
    ]


def _plan_with_entries(
    db: Any, tenant_id: RecordID, user_id: RecordID, *, lessons: int = 2
) -> tuple[RecordID, list[RecordID]]:
    """Cria tema + material + plano com `lessons` entradas; devolve (plan_id, entry_ids)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = [
        ParsedChapter(seq=i, title=f"Capítulo {i}", content=f"Conteúdo {i}.", part=None)
        for i in range(1, lessons + 1)
    ]
    sections = {
        ch.seq: [
            SectionPart(
                title=f"{ch.title} — A",
                anchor_text=ch.content,
                content=ch.content,
                summary=f"Sumário de {ch.title}",
            )
        ]
        for ch in chapters
    }
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
        chapters=chapters,
        sections=sections,
        summary="Um livro.",
    )
    all_sections = list_all_sections(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material.id
    )
    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        entries=[(f"Lição {i + 1}", [all_sections[i].id]) for i in range(lessons)],
    )
    return plan.id, [e.id for e in entries]


def _filled_lesson(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    entry_id: RecordID,
    *,
    day: int = 4,
) -> RecordID:
    """Cria e preenche uma lição (como o scheduler faz com o Tutor)."""
    lesson_id = create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entry_id,
        scheduled_for=datetime(2026, 8, day, 12, 0, tzinfo=timezone.utc),
    )
    fill_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        concept="Conceito destilado.",
        scenario="Cenário.",
        application="Aplicação.",
        recap=None,
        quiz=_quiz(),
        provenance=[{"chapter_seq": 1, "section_seq": 1, "quote": "trecho"}],
    )
    return lesson_id


# --- Leitura da lição -------------------------------------------------------------------


def test_get_lesson_returns_content_and_quiz(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A lição preenchida volta com os 4 blocos, quiz e proveniência (a UI precisa disso)."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    lesson = get_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson_id)
    assert lesson is not None
    assert lesson.concept == "Conceito destilado."
    assert lesson.scenario == "Cenário."
    assert lesson.application == "Aplicação."
    assert len(lesson.quiz) == 2
    assert lesson.quiz[0]["question"] == "O que é um Tema?"
    assert lesson.provenance[0]["section_seq"] == 1
    assert lesson.is_placeholder is False


def test_get_lesson_of_other_user_is_none(
    db: Any, tenant_id: RecordID, user_id: RecordID, other_user_id: RecordID
) -> None:
    """Lição de outro usuário é INEXISTENTE, não negada (a rota vira 404)."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    assert get_lesson(db, tenant_id=tenant_id, user_id=other_user_id, lesson_id=lesson_id) is None


def test_placeholder_lesson_is_flagged(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Lição criada e não preenchida (Tutor falhou) é placeholder, não lição vazia."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entries[0],
        scheduled_for=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    lesson = get_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson_id)
    assert lesson is not None
    assert lesson.is_placeholder is True
    assert lesson.quiz == []


def test_placeholder_does_not_count_as_done(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Placeholder NÃO conta como lição gerada — senão o scheduler pula a entrada.

    `create_lesson` grava `concept = ''`; a comparação com NONE dava `'' != NONE`
    (verdadeiro), então o placeholder contava e a entrada era abandonada vazia.
    """
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entries[0],
        scheduled_for=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    assert count_lessons_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id) == 0


def test_pending_lesson_is_found_for_retry(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """A lição placeholder é encontrável para re-tentativa do Tutor."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entries[0],
        scheduled_for=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    found = get_pending_lesson_for_entry(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id, plan_entry_id=entries[0]
    )
    assert found is not None
    assert str(found) == str(lesson_id)


def test_filled_lesson_counts_and_is_not_pending(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Depois do fill, a lição conta como gerada e não aparece como pendente."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    assert count_lessons_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id) == 1
    assert (
        get_pending_lesson_for_entry(
            db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id, plan_entry_id=entries[0]
        )
        is None
    )


def test_list_lessons_for_plan_is_ordered_by_date(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A timeline vem na ordem de estudo (data crescente), não na de criação."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    _filled_lesson(db, tenant_id, user_id, plan_id, entries[1], day=6)
    _filled_lesson(db, tenant_id, user_id, plan_id, entries[0], day=4)
    lessons = list_lessons_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    assert [lesson.scheduled_for.day for lesson in lessons] == [4, 6]


# --- Registro de estudo -----------------------------------------------------------------


def test_create_study_log_persists_performance(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O registro guarda respostas, acertos e reação — o dado que torna o estudo adaptativo."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    log = create_study_log(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        answers=[0, 0],
        correct_count=1,
        reaction="dificil",
    )
    assert log.answers == [0, 0]
    assert log.correct_count == 1
    assert log.reaction == "dificil"
    stored = get_study_log(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson_id)
    assert stored is not None
    assert stored.correct_count == 1


def test_study_log_without_reaction_is_allowed(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Reação é opcional (o dono pode concluir sem dizer se achou fácil)."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    log = create_study_log(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        answers=[0, 1],
        correct_count=2,
        reaction=None,
    )
    assert log.reaction is None


def test_second_study_log_is_refused(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Um registro POR LIÇÃO: o segundo envio não reescreve o desempenho já usado."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    create_study_log(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        answers=[0, 1],
        correct_count=2,
        reaction=None,
    )
    with pytest.raises(StoreError):
        create_study_log(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            lesson_id=lesson_id,
            answers=[1, 0],
            correct_count=0,
            reaction=None,
        )


def test_study_log_of_other_user_is_refused(
    db: Any, tenant_id: RecordID, user_id: RecordID, other_user_id: RecordID
) -> None:
    """Não se registra estudo na lição de outro usuário."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    with pytest.raises(StoreError):
        create_study_log(
            db,
            tenant_id=tenant_id,
            user_id=other_user_id,
            lesson_id=lesson_id,
            answers=[0, 1],
            correct_count=2,
            reaction=None,
        )


def test_logs_for_plan_are_indexed_by_lesson(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A timeline precisa saber, em uma leitura, quais lições já foram concluídas."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    first = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0], day=4)
    _filled_lesson(db, tenant_id, user_id, plan_id, entries[1], day=6)
    create_study_log(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=first,
        answers=[0, 1],
        correct_count=2,
        reaction="ok",
    )
    logs = list_study_logs_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    assert str(first) in logs
    assert len(logs) == 1


def test_progress_counts_completed_lessons(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """O progresso do tema anda quando o registro de estudo existe (antes era sempre 0/N)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = [ParsedChapter(seq=1, title="Cap 1", content="Conteúdo.", part=None)]
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=10,
        chapters=chapters,
        sections={
            1: [
                SectionPart(
                    title="A", anchor_text="Conteúdo.", content="Conteúdo.", summary="Sumário"
                )
            ]
        },
        summary="Livro.",
    )
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=material.id)
    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        entries=[("Lição 1", [sections[0].id])],
    )
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan.id, entries[0].id)
    before = get_topic_progress(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert before.done == 0
    create_study_log(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        answers=[0, 1],
        correct_count=2,
        reaction=None,
    )
    after = get_topic_progress(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert after.done == 1
    assert after.total == 1


# --- Recapitulação (misses) -------------------------------------------------------------


def test_recent_misses_returns_wrong_question_texts(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Os enunciados errados voltam para alimentar a recapitulação do Tutor.

    Sem isto o scheduler chamava o Tutor com `misses=()` fixo e a adaptação
    prometida no glossário nunca acontecia.
    """
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    create_study_log(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        answers=[1, 1],  # 1ª errada (gabarito 0), 2ª certa
        correct_count=1,
        reaction=None,
    )
    misses = recent_misses_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    assert misses == ["O que é um Tema?"]


def test_recent_misses_is_empty_without_errors(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Quem acertou tudo não recebe recapitulação."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    lesson_id = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0])
    create_study_log(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        answers=[0, 1],
        correct_count=2,
        reaction=None,
    )
    assert recent_misses_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id) == []


def test_recent_misses_prefers_recent_and_caps(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Erros mais recentes primeiro, com teto — a recapitulação é da lição do dia."""
    plan_id, entries = _plan_with_entries(db, tenant_id, user_id)
    older = _filled_lesson(db, tenant_id, user_id, plan_id, entries[0], day=4)
    newer = _filled_lesson(db, tenant_id, user_id, plan_id, entries[1], day=6)
    for lesson_id in (older, newer):
        create_study_log(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            lesson_id=lesson_id,
            answers=[1, 0],  # erra as duas
            correct_count=0,
            reaction=None,
        )
    misses = recent_misses_for_plan(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id, limit=3
    )
    assert len(misses) == 3  # 4 erros, teto de 3
    # A lição mais recente (day=6) contribui primeiro.
    assert misses[0] == "O que é um Tema?"
