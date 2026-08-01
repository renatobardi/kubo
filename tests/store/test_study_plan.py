"""KUBO-164 — Store de Plano: transição de estado, proposta, cadência.

Integração (SurrealDB real). Ao fechar um Tema, o planner propõe um Plano
que é persistido em `study_plan` (status='proposed') + `plan_entry` (lições).
A cadência (weekdays) é definida pelo dono e recalcula a data-alvo.
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
    create_material,
    create_topic,
    get_plan_for_topic,
    get_topic,
    save_plan_proposal,
    set_plan_cadence,
    set_topic_state,
)
from kubo.study.parsing import ParsedChapter

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_plan"


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


def _topic_with_material(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> tuple[RecordID, RecordID]:
    """Cria um tema com 1 material e 3 capítulos; retorna (topic_id, material_id)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
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
        chapters=_chapters(),
        summary="Um livro sobre agentes.",
    )
    return topic.id, material.id


# --- set_topic_state --------------------------------------------------------------------


def test_set_topic_state_transitions_draft_to_planning(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Transição draft → planning."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Estudo")
    set_topic_state(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id, state="planning")

    updated = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert updated is not None
    assert updated.state == "planning"


def test_set_topic_state_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro não pode transicionar tema alheio."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Meu")
    other = tenancy.create_user(db, firebase_uid="other-plan-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    with pytest.raises(StoreError):
        set_topic_state(
            db, tenant_id=tenant_id, user_id=other.id, topic_id=topic.id, state="planning"
        )


# --- save_plan_proposal + get_plan_for_topic --------------------------------------------


def test_save_plan_proposal_creates_plan_and_entries(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Proposta persistida cria study_plan (proposed) + plan_entry por lição."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    # Busca os RecordIDs dos capítulos.
    rows = db.query(
        "SELECT * FROM material_chapter WHERE material = $m ORDER BY seq;",
        {"m": material_id},
    )
    chapter_ids = [row["id"] for row in rows]

    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[
            ("Lição 1: Intro", [chapter_ids[0], chapter_ids[1]]),
            ("Lição 2: Avanço", [chapter_ids[2]]),
        ],
    )

    assert plan.status == "proposed"
    assert plan.topic == topic_id
    assert len(entries) == 2
    assert entries[0].seq == 1
    assert entries[0].title == "Lição 1: Intro"
    assert entries[0].chapters == [chapter_ids[0], chapter_ids[1]]
    assert entries[1].seq == 2
    assert entries[1].title == "Lição 2: Avanço"
    assert entries[1].chapters == [chapter_ids[2]]


def test_get_plan_for_topic_returns_none_if_no_plan(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Tema sem plano devolve (None, [])."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Sem plano")
    plan, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert plan is None
    assert entries == []


def test_get_plan_for_topic_returns_saved_plan(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """get_plan_for_topic devolve o plano e as lições persistidas."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    rows = db.query(
        "SELECT * FROM material_chapter WHERE material = $m ORDER BY seq;",
        {"m": material_id},
    )
    chapter_ids = [row["id"] for row in rows]

    save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Lição única", chapter_ids)],
    )

    plan, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert plan is not None
    assert plan.status == "proposed"
    assert len(entries) == 1
    assert entries[0].title == "Lição única"
    assert entries[0].chapters == chapter_ids


def test_save_plan_proposal_replaces_existing(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Salvar nova proposta substitui o plano anterior (1 plano por tema, UNIQUE)."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    rows = db.query(
        "SELECT * FROM material_chapter WHERE material = $m ORDER BY seq;",
        {"m": material_id},
    )
    chapter_ids = [row["id"] for row in rows]

    save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Velha", chapter_ids)],
    )
    save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Nova 1", [chapter_ids[0]]), ("Nova 2", [chapter_ids[1], chapter_ids[2]])],
    )

    plan, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert plan is not None
    assert len(entries) == 2
    assert entries[0].title == "Nova 1"
    assert entries[1].title == "Nova 2"


# --- set_plan_cadence -------------------------------------------------------------------


def test_set_plan_cadence_updates_weekdays_and_target_date(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Definir cadência atualiza weekdays e calcula data-alvo."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    rows = db.query(
        "SELECT * FROM material_chapter WHERE material = $m ORDER BY seq;",
        {"m": material_id},
    )
    chapter_ids = [row["id"] for row in rows]

    plan, _ = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [chapter_ids[0]]), ("L2", [chapter_ids[1]]), ("L3", [chapter_ids[2]])],
    )

    set_plan_cadence(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        weekdays=["mon", "wed", "fri"],
    )

    updated_plan, _ = get_plan_for_topic(
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id
    )
    assert updated_plan is not None
    assert sorted(updated_plan.weekdays) == ["fri", "mon", "wed"]
    assert updated_plan.target_date is not None


def test_set_plan_cadence_rejects_invalid_weekday(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Dia inválido é ValueError (não ignorado em silêncio)."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    rows = db.query(
        "SELECT * FROM material_chapter WHERE material = $m ORDER BY seq;",
        {"m": material_id},
    )
    chapter_ids = [row["id"] for row in rows]

    plan, _ = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", chapter_ids)],
    )

    with pytest.raises(ValueError, match="inválido"):
        set_plan_cadence(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            plan_id=plan.id,
            weekdays=["funday"],
        )
