"""Store integration tests for KUBO-167: archive/unarchive/delete/progress."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    activate_plan,
    archive_topic,
    create_lesson,
    create_material,
    create_topic,
    delete_topic,
    get_topic,
    get_topic_delete_summary,
    get_topic_progress,
    list_all_sections,
    list_archived_topics,
    list_topics,
    list_topics_by_state,
    save_plan_proposal,
    set_plan_cadence,
    set_topic_state,
    transition_to_running,
    unarchive_topic,
)
from kubo.study.parsing import ParsedChapter, SectionPart

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_archive"


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


def _chapters(n: int = 3) -> list[ParsedChapter]:
    return [
        ParsedChapter(seq=i, title=f"Capítulo {i}", content=f"Conteúdo {i}.", part=None)
        for i in range(1, n + 1)
    ]


def _sections_for(chapter: ParsedChapter) -> list[SectionPart]:
    """2 seções por capítulo — padrão do sectionizer."""
    content = chapter.content
    half = len(content) // 2
    return [
        SectionPart(
            title=f"{chapter.title} — Parte A",
            anchor_text=content[:half],
            content=content[:half],
            summary=f"Sumário A de {chapter.title}",
        ),
        SectionPart(
            title=f"{chapter.title} — Parte B",
            anchor_text=content[half:],
            content=content[half:],
            summary=f"Sumário B de {chapter.title}",
        ),
    ]


def _sections_map(chapters: list[ParsedChapter]) -> dict[int, list[SectionPart]]:
    return {ch.seq: _sections_for(ch) for ch in chapters}


def _topic_with_material(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> tuple[RecordID, RecordID]:
    """Cria um tema com 1 material, 3 capítulos e 6 seções; retorna (topic_id, material_id)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = _chapters()
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
        sections=_sections_map(chapters),
        summary="Um livro sobre agentes.",
    )
    return topic.id, material.id


def _topic_with_plan(db: Any, tenant_id: RecordID, user_id: RecordID) -> tuple[RecordID, RecordID]:
    """Cria tema + material + plano com 2 entries; retorna (topic_id, plan_id)."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=material_id)
    sids = [s.id for s in sections]
    plan, _entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[
            ("Lição 1", [sids[0], sids[1], sids[2]]),
            ("Lição 2", [sids[3], sids[4], sids[5]]),
        ],
    )
    set_topic_state(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id, state="planning")
    return topic_id, plan.id


# --- archive_topic -----------------------------------------------------------------------


def test_archive_topic_sets_archived_and_remembers_previous_state(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Arquivar seta state='archived' e grava archived_from com o estado anterior."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    set_topic_state(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id, state="planning")

    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)

    after = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert after is not None
    assert after.state == "archived"
    # archived_from gravado no banco.
    row = db.query("SELECT archived_from FROM $topic;", {"topic": topic.id})
    assert row[0]["archived_from"] == "planning"


def test_archive_topic_rejects_already_archived(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Arquivar tema já arquivado levanta StoreError."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)

    with pytest.raises(StoreError, match="arquivado"):
        archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)


def test_archive_topic_rejects_unknown_topic(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Arquivar tema inexistente levanta StoreError."""
    fake = RecordID("topic", "fake000")
    with pytest.raises(StoreError, match="tema não encontrado"):
        archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=fake)


def test_archive_topic_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro não pode arquivar tema alheio."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Meu")
    other = tenancy.create_user(db, firebase_uid="other-archive-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    with pytest.raises(StoreError):
        archive_topic(db, tenant_id=tenant_id, user_id=other.id, topic_id=topic.id)


# --- unarchive_topic ---------------------------------------------------------------------


def test_unarchive_topic_restores_previous_state(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Desarquivar restaura o estado anterior (archived_from) e limpa o campo."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    set_topic_state(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id, state="scheduled")
    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)

    unarchive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)

    after = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert after is not None
    assert after.state == "scheduled"
    row = db.query("SELECT archived_from FROM $topic;", {"topic": topic.id})
    assert row[0]["archived_from"] is None


def test_unarchive_topic_rejects_non_archived(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Desarquivar tema não-arquivado levanta StoreError."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    with pytest.raises(StoreError, match="não está arquivado"):
        unarchive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)


# --- list_archived_topics ----------------------------------------------------------------


def test_list_archived_topics_returns_only_archived(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """list_archived_topics retorna só os arquivados, não os ativos."""
    active = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Ativo")
    archived1 = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Arq 1")
    archived2 = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Arq 2")
    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=archived1.id)
    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=archived2.id)

    result = list_archived_topics(db, tenant_id=tenant_id, user_id=user_id)

    ids = {str(t.id) for t in result}
    assert ids == {str(archived1.id), str(archived2.id)}
    assert str(active.id) not in ids


def test_list_archived_topics_excludes_active(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """list_topics (ativos) não inclui arquivados e vice-versa."""
    active = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Ativo")
    archived = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Arq")
    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=archived.id)

    active_list = list_topics(db, tenant_id=tenant_id, user_id=user_id)
    archived_list = list_archived_topics(db, tenant_id=tenant_id, user_id=user_id)

    assert {str(t.id) for t in active_list} == {str(active.id)}
    assert {str(t.id) for t in archived_list} == {str(archived.id)}


# --- delete_topic ------------------------------------------------------------------------


def test_delete_topic_cascade_removes_everything(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """delete_topic remove topic + materials + chapters + plan + entries + lessons +
    study_log + chat (cascade total, ADR-0047 §4)."""
    topic_id, plan_id = _topic_with_plan(db, tenant_id, user_id)
    # Cria uma mensagem de chat.
    db.query(
        "CREATE study_chat SET tenant_id = $t, user_id = $u, topic = $topic, "
        "phase = 'draft', role = 'user', content = 'oi', created_at = time::now();",
        {"t": tenant_id, "u": user_id, "topic": topic_id},
    )
    # Cria uma lição + study_log (cascade deve remover ambos).
    entry_rows = db.query(
        "SELECT * FROM plan_entry WHERE study_plan = $p ORDER BY seq;", {"p": plan_id}
    )
    lesson = create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entry_rows[0]["id"],
        scheduled_for=datetime(2026, 8, 3),
    )
    assert lesson is not None
    db.query(
        "CREATE study_log SET tenant_id = $t, user_id = $u, lesson = $l, "
        "answers = [], correct_count = 0, completed_at = time::now();",
        {"t": tenant_id, "u": user_id, "l": lesson},
    )

    delete_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    # Topic gone.
    assert get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id) is None
    # Materials gone.
    mats = db.query("SELECT * FROM material WHERE topic = $topic;", {"topic": topic_id})
    assert mats == []
    # Chapters gone.
    chaps = db.query(
        "SELECT * FROM material_chapter WHERE tenant_id = $t AND user_id = $u;",
        {"t": tenant_id, "u": user_id},
    )
    assert chaps == []
    # Plan gone.
    plans = db.query("SELECT * FROM study_plan WHERE topic = $topic;", {"topic": topic_id})
    assert plans == []
    # Entries gone.
    entries = db.query(
        "SELECT * FROM plan_entry WHERE tenant_id = $t AND user_id = $u;",
        {"t": tenant_id, "u": user_id},
    )
    assert entries == []
    # Lessons gone.
    lessons = db.query(
        "SELECT * FROM lesson WHERE tenant_id = $t AND user_id = $u;",
        {"t": tenant_id, "u": user_id},
    )
    assert lessons == []
    # Study_log gone (cascade total — ADR-0047 §4).
    logs = db.query(
        "SELECT * FROM study_log WHERE tenant_id = $t AND user_id = $u;",
        {"t": tenant_id, "u": user_id},
    )
    assert logs == []
    # Chat gone.
    chats = db.query("SELECT * FROM study_chat WHERE topic = $topic;", {"topic": topic_id})
    assert chats == []


def test_delete_topic_rejects_unknown(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Deletar tema inexistente levanta StoreError."""
    fake = RecordID("topic", "fake000")
    with pytest.raises(StoreError, match="tema não encontrado"):
        delete_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=fake)


def test_delete_topic_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro não pode deletar tema alheio."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Meu")
    other = tenancy.create_user(db, firebase_uid="other-delete-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    with pytest.raises(StoreError):
        delete_topic(db, tenant_id=tenant_id, user_id=other.id, topic_id=topic.id)


# --- get_topic_delete_summary ------------------------------------------------------------


def test_get_topic_delete_summary_counts_dependents(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Summary retorna contagem de materials, lessons, chat messages."""
    topic_id, _ = _topic_with_plan(db, tenant_id, user_id)
    db.query(
        "CREATE study_chat SET tenant_id = $t, user_id = $u, topic = $topic, "
        "phase = 'draft', role = 'user', content = 'oi', created_at = time::now();",
        {"t": tenant_id, "u": user_id, "topic": topic_id},
    )

    summary = get_topic_delete_summary(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    assert summary.materials == 1
    assert summary.plan_entries == 2
    assert summary.lessons == 0
    assert summary.chat_messages == 1


def test_get_topic_delete_summary_empty_topic(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Summary de tema vazio: tudo zero."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Vazio")

    summary = get_topic_delete_summary(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)

    assert summary.materials == 0
    assert summary.plan_entries == 0
    assert summary.lessons == 0
    assert summary.chat_messages == 0


# --- get_topic_progress ------------------------------------------------------------------


def test_get_topic_progress_no_plan(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Tema sem plano: progresso 0/0, sem próxima lição."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")

    progress = get_topic_progress(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)

    assert progress.done == 0
    assert progress.total == 0
    assert progress.next_lesson_id is None


def test_get_topic_progress_with_plan_no_lessons(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Tema com plano de 2 entries, sem lições geradas: 0/2."""
    topic_id, _ = _topic_with_plan(db, tenant_id, user_id)

    progress = get_topic_progress(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    assert progress.done == 0
    assert progress.total == 2
    assert progress.next_lesson_id is None  # nenhuma lição gerada ainda


def test_get_topic_progress_with_lessons(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Tema running com 2 lições geradas, 1 concluída: 1/2, próxima = 2ª."""
    topic_id, plan_id = _topic_with_plan(db, tenant_id, user_id)
    set_plan_cadence(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id, weekdays=["mon", "wed"]
    )
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    # Busca entries do plano.
    plan_rows = db.query(
        "SELECT * FROM plan_entry WHERE study_plan = $p ORDER BY seq;", {"p": plan_id}
    )
    entry_id = plan_rows[0]["id"]
    # Transiciona para running + cria 1ª lição.
    transition_to_running(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        plan_id=plan_id,
        plan_entry_id=entry_id,
        scheduled_for=datetime(2026, 8, 3),
    )
    # Cria 2ª lição.
    entry_id_2 = plan_rows[1]["id"]
    create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entry_id_2,
        scheduled_for=datetime(2026, 8, 5),
    )
    # Marca 1ª lição como concluída (study_log).
    lesson_rows = db.query(
        "SELECT * FROM lesson WHERE study_plan = $p ORDER BY scheduled_for;", {"p": plan_id}
    )
    first_lesson_id = lesson_rows[0]["id"]
    db.query(
        "CREATE study_log SET tenant_id = $t, user_id = $u, lesson = $l, "
        "answers = [], correct_count = 0, completed_at = time::now();",
        {"t": tenant_id, "u": user_id, "l": first_lesson_id},
    )

    progress = get_topic_progress(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    assert progress.done == 1
    assert progress.total == 2
    assert progress.next_lesson_id == lesson_rows[1]["id"]


# --- scheduler respects archived ---------------------------------------------------------


def test_archived_topic_not_found_by_scheduler(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Tema arquivado não aparece em list_topics_by_state('running')."""
    topic_id, plan_id = _topic_with_plan(db, tenant_id, user_id)
    set_plan_cadence(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id, weekdays=["mon", "wed"]
    )
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    # Arquiva diretamente (simula scheduler pausado).
    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    running = list_topics_by_state(db, tenant_id=tenant_id, user_id=user_id, state="running")
    scheduled = list_topics_by_state(db, tenant_id=tenant_id, user_id=user_id, state="scheduled")

    assert str(topic_id) not in {str(t.id) for t in running}
    assert str(topic_id) not in {str(t.id) for t in scheduled}


def test_unarchive_topic_scheduler_resumes(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Desarquivar restaura o estado anterior e o scheduler volta a encontrar o tema."""
    topic_id, plan_id = _topic_with_plan(db, tenant_id, user_id)
    set_plan_cadence(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id, weekdays=["mon", "wed"]
    )
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    # Arquiva (scheduler pausa).
    archive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    scheduled = list_topics_by_state(db, tenant_id=tenant_id, user_id=user_id, state="scheduled")
    assert str(topic_id) not in {str(t.id) for t in scheduled}
    # Desarquiva (scheduler retoma).
    unarchive_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    scheduled = list_topics_by_state(db, tenant_id=tenant_id, user_id=user_id, state="scheduled")
    assert str(topic_id) in {str(t.id) for t in scheduled}
