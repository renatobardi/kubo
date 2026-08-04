"""KUBO-189 — Integration test: scheduler → tutor → lesson with section provenance.

Exercita o flow completo com DB real:
1. Cria material com capítulos + seções
2. Salva plano com sections no plan_entry
3. Chama _generate_lesson_content (scheduler) com tutor mockado
4. Verifica que fill_lesson recebeu provenance com (chapter_seq, section_seq)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, migrations
from kubo.store.study import (
    create_material,
    create_topic,
    list_all_sections,
    save_plan_proposal,
)
from kubo.study.parsing import ParsedChapter, SectionPart

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_lessons_integration"


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


def _chapters(n: int = 2) -> list[ParsedChapter]:
    return [
        ParsedChapter(seq=i, title=f"Capítulo {i}", content=f"Conteúdo do capítulo {i}.", part=None)
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


def test_generate_lesson_content_uses_sections_with_provenance(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flow completo: plan_entry com sections → _generate_lesson_content → fill_lesson.

    O tutor mockado recebe sections (não chapters) e devolve provenance com
    (chapter_seq, section_seq). A lição persistida tem a provenance correta.
    """
    from kubo.scheduler import study_lessons
    from kubo.study.tutor import LessonOutput, ProvenanceItem, QuizItem

    # 1. Cria tema + material com 2 capítulos × 2 seções = 4 seções.
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = _chapters(2)
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

    # 2. Busca as seções e salva plano com as 2 primeiras seções.
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=material.id)
    assert len(sections) == 4
    # sections[0] e sections[1] são do capítulo 1 (chapter_seq=1, seq=1 e 2)
    assert sections[0].chapter_seq == 1
    assert sections[1].chapter_seq == 1

    _, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        entries=[("Lição 1", [sections[0].id, sections[1].id])],
    )
    entry = entries[0]

    # 3. Cria lição vazia e chama _generate_lesson_content com tutor mockado.
    from kubo.store.study import create_lesson

    lesson_id = create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=entries[0].study_plan,
        plan_entry_id=entry.id,
        scheduled_for=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )

    # Tutor mockado: devolve lição com provenance referenciando (1, 1) e (1, 2).
    lesson_output = LessonOutput(
        concept="Conceito sobre a seção.",
        scenario="Cenário prático.",
        application="Como aplicar.",
        recap=None,
        provenance=[
            ProvenanceItem(chapter_seq=1, section_seq=1, quote="trecho da seção 1"),
            ProvenanceItem(chapter_seq=1, section_seq=2, quote="trecho da seção 2"),
        ],
        quiz=[
            QuizItem(question="Q1?", options=["A", "B"], explanation="E1", answer_index=0),
            QuizItem(question="Q2?", options=["C", "D"], explanation="E2", answer_index=1),
        ],
    )

    class _FakeTutor:
        def generate(self, **kw: object) -> LessonOutput:
            # Verifica que recebeu sections (não chapters) com chapter_seq correto.
            sections_arg = kw.get("sections")
            assert sections_arg is not None, "tutor deve receber sections, não chapters"
            sections_list = list(sections_arg)  # type: ignore[arg-type]
            assert len(sections_list) == 2
            assert sections_list[0].chapter_seq == 1  # type: ignore[union-attr]
            assert sections_list[0].seq == 1  # type: ignore[union-attr]
            assert sections_list[1].chapter_seq == 1  # type: ignore[union-attr]
            assert sections_list[1].seq == 2  # type: ignore[union-attr]
            return lesson_output

    # Mocka apenas a construção do Tutor e work_context — o store é real.
    monkeypatch.setattr(study_lessons, "_build_tutor", lambda db, tenant_id, user_id: _FakeTutor())
    monkeypatch.setattr(study_lessons, "_work_context_for", lambda db, user_id: "")

    study_lessons._generate_lesson_content(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        entry=entry,
        work_context="",
    )

    # 4. Verifica que a lição foi preenchida com a provenance de seções.
    rows: list[dict[str, Any]] = db.query(
        "SELECT concept, provenance FROM lesson WHERE id = $lid;", {"lid": lesson_id}
    )
    assert rows, "lição deve existir"
    row = rows[0]
    assert row["concept"] == "Conceito sobre a seção."
    assert len(row["provenance"]) == 2
    assert row["provenance"][0]["chapter_seq"] == 1
    assert row["provenance"][0]["section_seq"] == 1
    assert row["provenance"][1]["chapter_seq"] == 1
    assert row["provenance"][1]["section_seq"] == 2
