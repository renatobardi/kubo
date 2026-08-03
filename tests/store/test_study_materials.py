"""KUBO-162 — Store de Material: upload dentro de Tema, lista, delete, sumário.

Integração (SurrealDB real). Material é exclusivo a um Tema (N:1, ADR-0047):
nasce de um upload dentro de um Tema em `draft`, com capítulos parseados e
sumário gerado síncrono.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    count_materials_by_topic,
    create_material,
    create_topic,
    delete_material,
    list_materials_by_topic,
)
from kubo.study.parsing import ParsedChapter

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_materials"


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
    """Capítulos de teste (não vêm de parse real)."""
    return [
        ParsedChapter(seq=i, title=f"Capítulo {i}", content=f"Conteúdo do capítulo {i}.", part=None)
        for i in range(n)
    ]


# --- create_material com topic_id + summary ---------------------------------------------


def test_create_material_with_topic_and_summary(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Material é criado dentro de um Tema com topic_id e sumário."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Meu estudo")
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Manual de Kubo",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/materials/t/u/abc.epub",
        size_bytes=1024,
        chapters=_chapters(),
        sections=None,
        summary="Um guia sobre agentes e ateliê.",
    )

    assert material.topic == topic.id
    assert material.summary == "Um guia sobre agentes e ateliê."
    assert material.title == "Manual de Kubo"
    assert material.chapter_count == 2


def test_create_material_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro do MESMO tenant não vê material alheio."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Privado")
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Material privado",
        fmt="epub",
        original_filename="priv.epub",
        file_path="/data/p.epub",
        size_bytes=512,
        chapters=_chapters(1),
        sections=None,
        summary="Resumo privado.",
    )
    other = tenancy.create_user(db, firebase_uid="other-mat-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    materials = list_materials_by_topic(
        db, tenant_id=tenant_id, user_id=other.id, topic_id=topic.id
    )
    assert materials == []


# --- list_materials_by_topic ------------------------------------------------------------


def test_list_materials_by_topic(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Lista os materiais de um Tema, mais recentes primeiro."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    first = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Primeiro",
        fmt="epub",
        original_filename="1.epub",
        file_path="/data/1.epub",
        size_bytes=100,
        chapters=_chapters(1),
        sections=None,
        summary="R1",
    )
    second = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Segundo",
        fmt="pdf",
        original_filename="2.pdf",
        file_path="/data/2.pdf",
        size_bytes=200,
        chapters=_chapters(1),
        sections=None,
        summary="R2",
    )

    materials = list_materials_by_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert [m.id for m in materials] == [second.id, first.id]


def test_list_materials_by_topic_excludes_other_topics(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Materiais de outro Tema não aparecem na lista."""
    topic_a = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema A")
    topic_b = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema B")
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_a.id,
        title="A",
        fmt="epub",
        original_filename="a.epub",
        file_path="/data/a.epub",
        size_bytes=100,
        chapters=_chapters(1),
        sections=None,
        summary="RA",
    )
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_b.id,
        title="B",
        fmt="epub",
        original_filename="b.epub",
        file_path="/data/b.epub",
        size_bytes=100,
        chapters=_chapters(1),
        sections=None,
        summary="RB",
    )

    materials = list_materials_by_topic(
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_a.id
    )
    assert len(materials) == 1
    assert materials[0].title == "A"


# --- delete_material --------------------------------------------------------------------


def test_delete_material_removes_record(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Delete remove o material e seus capítulos do banco."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Descartável",
        fmt="epub",
        original_filename="d.epub",
        file_path="/data/d.epub",
        size_bytes=100,
        chapters=_chapters(2),
        sections=None,
        summary="RD",
    )

    delete_material(db, tenant_id=tenant_id, user_id=user_id, material_id=material.id)

    materials = list_materials_by_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert materials == []


def test_delete_material_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro não pode deletar material alheio (StoreError)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="Meu",
        fmt="epub",
        original_filename="m.epub",
        file_path="/data/m.epub",
        size_bytes=100,
        chapters=_chapters(1),
        sections=None,
        summary="RM",
    )
    other = tenancy.create_user(db, firebase_uid="other-del-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    with pytest.raises(StoreError):
        delete_material(db, tenant_id=tenant_id, user_id=other.id, material_id=material.id)


# --- count_materials_by_topic -----------------------------------------------------------


def test_count_materials_by_topic(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Conta os materiais de um Tema (para validação de limite)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    assert (
        count_materials_by_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id) == 0
    )

    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="A",
        fmt="epub",
        original_filename="a.epub",
        file_path="/data/a.epub",
        size_bytes=100,
        chapters=_chapters(1),
        sections=None,
        summary="RA",
    )
    create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        title="B",
        fmt="pdf",
        original_filename="b.pdf",
        file_path="/data/b.pdf",
        size_bytes=200,
        chapters=_chapters(1),
        sections=None,
        summary="RB",
    )

    assert (
        count_materials_by_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id) == 2
    )
