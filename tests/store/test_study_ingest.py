"""KUBO-202 — Ingestão de Material em background (ADR-0049 §III).

Testes de integração do ciclo de vida de ingestão:
- `create_pending_material`: cria material sem chapters/sections, status=pending.
- `ingest_material`: parse + chapters + sections + summary → status=ready.
- `mark_material_failed`: status=failed + error.
- `list_pending_materials`: lista apenas pending.
- `retry_material_ingest`: failed → pending (retry manual ou backoff).
- `count_ready_materials_by_topic`: conta apenas ready.
- `create_material` (legado): status=ready (compatibilidade com testes existentes).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, migrations
from kubo.store.study import (
    count_ready_materials_by_topic,
    create_material,
    create_pending_material,
    create_topic,
    ingest_material,
    list_materials_by_topic,
    list_pending_materials,
    mark_material_failed,
    retry_material_ingest,
)
from kubo.study.parsing import ParsedChapter

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_ingest"


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


def _topic(db: Any, tenant_id: RecordID, user_id: RecordID) -> RecordID:
    return create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo").id


# --- create_pending_material -----------------------------------------------------------


def test_create_pending_material_has_pending_status(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Material criado via upload tem status=pending, sem chapters/sections."""
    topic_id = _topic(db, tenant_id, user_id)
    material = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/materials/livro.epub",
        size_bytes=1024,
    )
    assert material.status == "pending"
    assert material.error is None
    assert material.ingested_at is None
    assert material.chapter_count == 0


def test_create_pending_material_is_user_scoped(
    db: Any, tenant_id: RecordID, user_id: RecordID, other_user_id: RecordID
) -> None:
    """Material pending é isolado por usuário."""
    topic_id = _topic(db, tenant_id, user_id)
    create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
    )
    pending = list_pending_materials(db, tenant_id=tenant_id, user_id=other_user_id)
    assert pending == []


# --- ingest_material -------------------------------------------------------------------


def test_ingest_material_creates_chapters_and_marks_ready(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """ingest_material: parse → chapters + sections + summary → ready."""
    topic_id = _topic(db, tenant_id, user_id)
    material = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
    )
    ready = ingest_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material.id,
        chapters=_chapters(2),
        sections=None,
        summary="Um guia sobre agentes.",
    )
    assert ready.status == "ready"
    assert ready.error is None
    assert ready.ingested_at is not None
    assert ready.chapter_count == 2
    assert ready.summary == "Um guia sobre agentes."


def test_ingest_material_is_idempotent_on_ready(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Re-ingest de material já ready não duplica chapters/sections."""
    topic_id = _topic(db, tenant_id, user_id)
    material = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
    )
    ingest_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material.id,
        chapters=_chapters(2),
        sections=None,
        summary="Resumo.",
    )
    # 2ª chamada: deve ser no-op ou re-processar sem duplicar.
    ready = ingest_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material.id,
        chapters=_chapters(2),
        sections=None,
        summary="Resumo.",
    )
    assert ready.status == "ready"
    assert ready.chapter_count == 2  # não duplicou


# --- mark_material_failed --------------------------------------------------------------


def test_mark_material_failed_sets_error(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Falha de ingestão marca material como failed com motivo."""
    topic_id = _topic(db, tenant_id, user_id)
    material = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
    )
    failed = mark_material_failed(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material.id,
        error="Falha ao parsear epub.",
    )
    assert failed.status == "failed"
    assert failed.error == "Falha ao parsear epub."
    assert failed.ingested_at is None


# --- list_pending_materials ------------------------------------------------------------


def test_list_pending_materials_returns_only_pending(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """list_pending_materials filtra por status=pending."""
    topic_id = _topic(db, tenant_id, user_id)
    m1 = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro 1",
        fmt="epub",
        original_filename="livro1.epub",
        file_path="/data/livro1.epub",
        size_bytes=1024,
    )
    m2 = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro 2",
        fmt="epub",
        original_filename="livro2.epub",
        file_path="/data/livro2.epub",
        size_bytes=1024,
    )
    # m2 vira ready
    ingest_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=m2.id,
        chapters=_chapters(1),
        sections=None,
        summary="Resumo.",
    )
    pending = list_pending_materials(db, tenant_id=tenant_id, user_id=user_id)
    assert len(pending) == 1
    assert pending[0].id == m1.id


# --- retry_material_ingest -------------------------------------------------------------


def test_retry_material_ingest_resets_to_pending(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Retry de material failed volta para pending."""
    topic_id = _topic(db, tenant_id, user_id)
    material = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
    )
    mark_material_failed(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material.id,
        error="Timeout.",
    )
    retried = retry_material_ingest(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material.id,
    )
    assert retried.status == "pending"
    assert retried.error is None


# --- count_ready_materials_by_topic ----------------------------------------------------


def test_count_ready_materials_excludes_pending(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """count_ready_materials_by_topic só conta ready, não pending/failed."""
    topic_id = _topic(db, tenant_id, user_id)
    m1 = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro 1",
        fmt="epub",
        original_filename="livro1.epub",
        file_path="/data/livro1.epub",
        size_bytes=1024,
    )
    create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro 2",
        fmt="epub",
        original_filename="livro2.epub",
        file_path="/data/livro2.epub",
        size_bytes=1024,
    )
    # m1 vira ready
    ingest_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=m1.id,
        chapters=_chapters(1),
        sections=None,
        summary="Resumo.",
    )
    assert (
        count_ready_materials_by_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
        == 1
    )


# --- create_material (legado) marca ready ----------------------------------------------


def test_create_material_legacy_marks_ready(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """create_material (síncrono, legado) cria com status=ready."""
    topic_id = _topic(db, tenant_id, user_id)
    material = create_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
        chapters=_chapters(2),
        sections=None,
        summary="Resumo.",
    )
    assert material.status == "ready"
    assert material.ingested_at is not None


# --- list_materials_by_topic mostra status ---------------------------------------------


def test_list_materials_by_topic_shows_status(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """list_materials_by_topic devolve o status de cada material."""
    topic_id = _topic(db, tenant_id, user_id)
    m1 = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Pendente",
        fmt="epub",
        original_filename="p.epub",
        file_path="/data/p.epub",
        size_bytes=1024,
    )
    ingest_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=m1.id,
        chapters=_chapters(1),
        sections=None,
        summary="OK.",
    )
    m2 = create_pending_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        title="Falhado",
        fmt="epub",
        original_filename="f.epub",
        file_path="/data/f.epub",
        size_bytes=1024,
    )
    mark_material_failed(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=m2.id,
        error="Erro.",
    )
    materials = list_materials_by_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    statuses = {m.title: m.status for m in materials}
    assert statuses["Pendente"] == "ready"
    assert statuses["Falhado"] == "failed"
