"""KUBO-184 — Store de Sections: persistência e leitura de seções de capítulo.

Integração (SurrealDB real). Sections são persistidas junto com o material +
capítulos na mesma transação, no upload. Aditivo: o planner/tutor/scheduler
continuam operando em capítulos; as seções são persistidas para consumo futuro.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    create_material,
    create_topic,
    delete_material,
    list_all_sections,
    list_materials_by_topic,
)
from kubo.study.parsing import ParsedChapter, SectionPart

pytestmark = pytest.mark.integration

_SECTIONS_DB = "test_study_sections"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_SECTIONS_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_SECTIONS_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_SECTIONS_DB};")


def _chapters(n: int = 2) -> list[ParsedChapter]:
    """Capítulos de teste (seq 1-based, mesmo molde do parsing real)."""
    return [
        ParsedChapter(seq=i, title=f"Capítulo {i}", content=f"Conteúdo do capítulo {i}.", part=None)
        for i in range(1, n + 1)
    ]


def _sections_for(chapter: ParsedChapter) -> list[SectionPart]:
    """Seções de teste para um capítulo: 2 seções cuja união cobre o capítulo."""
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
    """Mapa chapter.seq → seções para todos os capítulos."""
    return {ch.seq: _sections_for(ch) for ch in chapters}


def _material_id(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, topic_id: RecordID
) -> RecordID:
    """Busca o material_id real (criado no teste) via list_materials_by_topic."""
    materials = list_materials_by_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert len(materials) == 1
    return materials[0].id


# --- create_material persiste sections --------------------------------------------------


def test_create_material_persists_sections(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """create_material persiste seções junto com capítulos na mesma transação."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = _chapters(2)
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Manual",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/manual.epub",
        size_bytes=1024,
        chapters=chapters,
        sections=_sections_map(chapters),
        summary="Um guia.",
    )

    mid = _material_id(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=mid)
    assert len(sections) == 4  # 2 capítulos × 2 seções


def test_create_material_without_sections_falls_back_to_one_per_chapter(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """sections=None → 1 seção fallback por capítulo (content = capítulo inteiro)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = _chapters(2)
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Manual",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/manual.epub",
        size_bytes=1024,
        chapters=chapters,
        sections=None,
        summary="Um guia.",
    )

    mid = _material_id(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=mid)
    assert len(sections) == 2  # 1 seção fallback por capítulo
    # A seção fallback cobre o capítulo inteiro.
    assert all(s.content.startswith("Conteúdo do capítulo") for s in sections)


# --- list_all_sections ------------------------------------------------------------------


def test_list_all_sections_returns_in_chapter_then_seq_order(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """list_all_sections devolve seções ordenadas por (chapter.seq, section.seq)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = _chapters(2)
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Manual",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/manual.epub",
        size_bytes=1024,
        chapters=chapters,
        sections=_sections_map(chapters),
        summary="Um guia.",
    )

    mid = _material_id(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=mid)

    # Ordenadas por (chapter.seq, section.seq): cap 1 seção 1, cap 1 seção 2,
    # cap 2 seção 1, cap 2 seção 2.
    assert len(sections) == 4
    assert sections[0].chapter_seq == 1
    assert sections[0].seq == 1
    assert sections[1].chapter_seq == 1
    assert sections[1].seq == 2
    assert sections[2].chapter_seq == 2
    assert sections[2].seq == 1
    assert sections[3].chapter_seq == 2
    assert sections[3].seq == 2


def test_list_all_sections_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Seções de outro usuário no mesmo tenant são invisíveis."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = _chapters(1)
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Manual",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/manual.epub",
        size_bytes=1024,
        chapters=chapters,
        sections=_sections_map(chapters),
        summary="Um guia.",
    )

    # Outro usuário no mesmo tenant.
    other_user = tenancy.create_user(db, firebase_uid="other-user-uid")
    tenancy.create_membership(db, user_id=other_user.id, tenant_id=tenant_id, role="member")

    mid = _material_id(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    # Outro usuário não vê as seções do material do primeiro usuário.
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=other_user.id, material_id=mid)
    assert sections == []


# --- delete_material apaga as seções ----------------------------------------------------


def test_delete_material_removes_sections(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """delete_material apaga as seções junto com capítulos e material (cascade)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    chapters = _chapters(1)
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Manual",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/manual.epub",
        size_bytes=1024,
        chapters=chapters,
        sections=_sections_map(chapters),
        summary="Um guia.",
    )

    mid = _material_id(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert len(list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=mid)) == 2

    delete_material(db, tenant_id=tenant_id, user_id=user_id, material_id=mid)

    assert list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=mid) == []
