"""Store de Estudos (ADR-0043, KUBO-135): Material e capítulos, user-scoped no tenant.

Integração (SurrealDB real). Material é dado PESSOAL: o isolamento que importa aqui não
é só entre tenants — é entre USUÁRIOS do mesmo tenant. Um teste que só provasse o corte
por tenant deixaria passar a leitura do material do colega de equipe.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import ConfigError, MembershipRequiredError, StoreError
from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    count_chapters,
    create_material,
    get_material,
    list_chapters,
    list_materials,
)
from kubo.study.parsing import ParsedChapter

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study"


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


def _chapters(count: int = 2) -> list[ParsedChapter]:
    """Capítulos de entrada, `seq` 1-based, o primeiro dentro de uma parte."""
    return [
        ParsedChapter(
            seq=i,
            title=f"Capítulo {i}",
            content=f"Conteúdo do capítulo {i}.",
            part="Parte I" if i == 1 else None,
        )
        for i in range(1, count + 1)
    ]


def _create(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    *,
    title: str = "Manual de Kubo",
    chapters: list[ParsedChapter] | None = None,
) -> Any:
    """Cria um material com os defaults do teste."""
    chs = _chapters() if chapters is None else chapters
    return create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/materials/t/u/m.epub",
        size_bytes=1024,
        chapters=chs,
    )


def _other_member(db: Any, tenant_id: RecordID) -> RecordID:
    """Segundo usuário, MEMBRO DO MESMO TENANT — o vizinho que não pode ver o material."""
    other = tenancy.create_user(db, firebase_uid="other-user-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")
    return other.id


def test_create_material_persists_material_and_chapters(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """create_material grava o material com chapter_count e todos os capítulos."""
    material = _create(db, tenant_id, user_id)

    assert material.title == "Manual de Kubo"
    assert material.fmt == "epub"
    assert material.chapter_count == 2
    assert material.tenant_id == tenant_id
    assert material.user_id == user_id
    assert count_chapters(db, tenant_id=tenant_id, user_id=user_id, material_id=material.id) == 2


def test_list_chapters_returns_reading_order_with_part(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Os capítulos voltam ordenados por `seq`, com `part` preservado."""
    material = _create(db, tenant_id, user_id)

    chapters = list_chapters(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material.id, limit=50, start=0
    )

    assert [c.seq for c in chapters] == [1, 2]
    assert [c.part for c in chapters] == ["Parte I", None]
    assert chapters[0].content == "Conteúdo do capítulo 1."


def test_list_chapters_paginates(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """limit/start recortam a página sem reordenar."""
    material = _create(db, tenant_id, user_id, chapters=_chapters(5))

    page = list_chapters(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material.id, limit=2, start=2
    )

    assert [c.seq for c in page] == [3, 4]


def test_list_and_get_material_scoped_to_owner(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """list/get devolvem o material do dono."""
    material = _create(db, tenant_id, user_id)

    listed = list_materials(db, tenant_id=tenant_id, user_id=user_id)
    fetched = get_material(db, tenant_id=tenant_id, user_id=user_id, material_id=material.id)

    assert [m.id for m in listed] == [material.id]
    assert fetched is not None
    assert fetched.id == material.id


def test_material_is_invisible_to_another_member_of_same_tenant(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Outro membro do MESMO tenant não lista nem lê o material — get devolve None."""
    material = _create(db, tenant_id, user_id)
    other_id = _other_member(db, tenant_id)

    assert list_materials(db, tenant_id=tenant_id, user_id=other_id) == []
    assert get_material(db, tenant_id=tenant_id, user_id=other_id, material_id=material.id) is None


def test_chapters_are_invisible_to_another_member_of_same_tenant(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O corte por usuário vale também para os capítulos, não só para o material."""
    material = _create(db, tenant_id, user_id)
    other_id = _other_member(db, tenant_id)

    assert (
        list_chapters(
            db, tenant_id=tenant_id, user_id=other_id, material_id=material.id, limit=50, start=0
        )
        == []
    )
    assert count_chapters(db, tenant_id=tenant_id, user_id=other_id, material_id=material.id) == 0


def test_create_material_is_atomic_on_duplicate_seq(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """seq duplicado viola o índice UNIQUE e NADA persiste — sem material fantasma."""
    duplicated = [
        ParsedChapter(seq=1, title="A", content="a", part=None),
        ParsedChapter(seq=1, title="B", content="b", part=None),
    ]

    with pytest.raises(StoreError):
        _create(db, tenant_id, user_id, chapters=duplicated)

    assert list_materials(db, tenant_id=tenant_id, user_id=user_id) == []
    assert db.query("SELECT * FROM material_chapter;") == []


def test_store_requires_tenant_and_membership(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Contrato KUBO-123: tenant_id/user_id obrigatórios e membership verificada."""
    with pytest.raises((TypeError, ConfigError)):
        create_material(  # type: ignore[reportCallIssue]
            db,
            title="X",
            fmt="epub",
            original_filename="x.epub",
            file_path="/data/materials/x.epub",
            size_bytes=1,
            chapters=_chapters(1),
        )

    stranger = tenancy.create_user(db, firebase_uid="stranger-uid").id
    with pytest.raises(MembershipRequiredError):
        list_materials(db, tenant_id=tenant_id, user_id=stranger)
