"""KUBO-164/185 — Store de Plano: transição de estado, proposta, cadência.

Integração (SurrealDB real). Ao fechar um Tema, o planner propõe um Plano
que é persistido em `study_plan` (status='proposed') + `plan_entry` (lições).
A cadência (weekdays) é definida pelo dono e recalcula a data-alvo.

KUBO-185: o átomo do plano é a seção (não o capítulo). `plan_entry.sections`
é uma lista de RecordIDs de `material_section`. A compatibilidade
`get_chapters_for_entry` deriva capítulos das seções (via FK `material_chapter`).
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
    activate_plan,
    count_lessons_for_plan,
    create_lesson,
    create_material,
    create_topic,
    deactivate_plan,
    get_chapters_for_entry,
    get_plan_for_topic,
    get_sections_for_entry,
    get_topic,
    list_all_sections,
    list_topics_by_state,
    remove_section_from_entry,
    replace_plan_entries,
    save_plan_proposal,
    set_plan_cadence,
    set_topic_state,
    swap_plan_entries,
    transition_to_running,
)
from kubo.study.parsing import ParsedChapter, SectionPart

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
    """Cria um tema com 1 material, 3 capítulos e 6 seções (2 por capítulo)."""
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


def _section_ids(
    db: Any, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> list[RecordID]:
    """Busca os RecordIDs das seções do material, na ordem (chapter_seq, section_seq)."""
    sections = list_all_sections(db, tenant_id=tenant_id, user_id=user_id, material_id=material_id)
    return [s.id for s in sections]


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
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[
            ("Lição 1: Intro", [sids[0], sids[1], sids[2]]),
            ("Lição 2: Avanço", [sids[3], sids[4], sids[5]]),
        ],
    )

    assert plan.status == "proposed"
    assert plan.topic == topic_id
    assert len(entries) == 2
    assert entries[0].seq == 1
    assert entries[0].title == "Lição 1: Intro"
    assert entries[0].sections == [sids[0], sids[1], sids[2]]
    assert entries[1].seq == 2
    assert entries[1].title == "Lição 2: Avanço"
    assert entries[1].sections == [sids[3], sids[4], sids[5]]


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
    sids = _section_ids(db, tenant_id, user_id, material_id)

    save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Lição única", sids)],
    )

    plan, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert plan is not None
    assert plan.status == "proposed"
    assert len(entries) == 1
    assert entries[0].title == "Lição única"
    assert entries[0].sections == sids


def test_save_plan_proposal_replaces_existing(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Salvar nova proposta substitui o plano anterior (1 plano por tema, UNIQUE)."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Velha", sids)],
    )
    save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Nova 1", [sids[0]]), ("Nova 2", [sids[1], sids[2]])],
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
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, _ = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0]]), ("L2", [sids[1]]), ("L3", [sids[2]])],
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
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, _ = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", sids)],
    )

    with pytest.raises(ValueError, match="inválido"):
        set_plan_cadence(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            plan_id=plan.id,
            weekdays=["funday"],
        )


# --- KUBO-165: edição manual do plano (swap, remove section, replace) --------------------


def test_swap_plan_entries_swaps_seqs(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Swap de duas entries troca os seqs atomicamente."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0]]), ("L2", [sids[1]]), ("L3", [sids[2]])],
    )

    swap_plan_entries(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        entry_a=entries[0].id,
        entry_b=entries[2].id,
    )

    _, updated = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert updated[0].seq == 1
    assert updated[0].title == "L3"
    assert updated[1].seq == 2
    assert updated[1].title == "L2"
    assert updated[2].seq == 3
    assert updated[2].title == "L1"


def test_swap_plan_entries_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro não pode swap de plano alheio."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)
    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0]]), ("L2", [sids[1]])],
    )
    other = tenancy.create_user(db, firebase_uid="other-swap-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    with pytest.raises(StoreError):
        swap_plan_entries(
            db,
            tenant_id=tenant_id,
            user_id=other.id,
            plan_id=plan.id,
            entry_a=entries[0].id,
            entry_b=entries[1].id,
        )


def test_remove_section_from_entry_updates_sections(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Remover seção de uma lição atualiza a lista de sections."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0], sids[1]]), ("L2", [sids[2]])],
    )

    ok = remove_section_from_entry(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        entry_id=entries[0].id,
        section_id=sids[1],
    )

    assert ok is True
    _, updated = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert updated[0].sections == [sids[0]]
    assert updated[1].sections == [sids[2]]


def test_remove_section_from_entry_rejects_last_section(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Remover a última seção de uma lição é rejeitado (não esvazia)."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0]])],
    )

    ok = remove_section_from_entry(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        entry_id=entries[0].id,
        section_id=sids[0],
    )

    assert ok is False
    _, updated = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert updated[0].sections == [sids[0]]


def test_remove_section_from_entry_scoped_to_owner(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Outro membro não pode remover seção de plano alheio."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)
    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0], sids[1]])],
    )
    other = tenancy.create_user(db, firebase_uid="other-rm-sec-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    with pytest.raises(StoreError):
        remove_section_from_entry(
            db,
            tenant_id=tenant_id,
            user_id=other.id,
            entry_id=entries[0].id,
            section_id=sids[0],
        )


def test_replace_plan_entries_preserves_cadence(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """replace_plan_entries preserva weekdays/target_date do plano existente."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, _ = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0]]), ("L2", [sids[1]])],
    )
    set_plan_cadence(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        weekdays=["mon", "wed"],
    )

    # Replace entries — cadência deve sobreviver.
    replace_plan_entries(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Tudo junto", sids)],
    )

    updated_plan, updated_entries = get_plan_for_topic(
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id
    )
    assert updated_plan is not None
    assert sorted(updated_plan.weekdays) == ["mon", "wed"]
    assert updated_plan.target_date is not None
    assert len(updated_entries) == 1
    assert updated_entries[0].title == "Tudo junto"


def test_replace_plan_entries_without_cadence_does_not_crash(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """replace_plan_entries com weekdays=[] (sem cadência) não crasha.

    Reproduce o bug de PRD: repropose chamava replace_plan_entries antes de
    o dono definir cadência. compute_target_date(weekdays=[]) levantava
    ValueError ("a cadência precisa de ao menos um dia da semana") → 500.
    Sem cadência, target_date fica None (sem data-alvo para calcular).
    """
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    plan, _ = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0]]), ("L2", [sids[1]])],
    )
    # Persiste um target_date não-nulo manualmente, mantendo weekdays=[].
    # Isso garante que o teste falha se replace_plan_entries parar de limpar
    # o target_date existente (o UPDATE ... SET target_date = NONE é essencial).
    from datetime import datetime

    db.query(
        "UPDATE $plan SET target_date = $target;",
        {"plan": plan.id, "target": datetime(2026, 12, 31)},
    )

    # Replace entries — não deve crashar mesmo sem cadência.
    replace_plan_entries(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Tudo junto", sids)],
    )

    updated_plan, updated_entries = get_plan_for_topic(
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id
    )
    assert updated_plan is not None
    assert updated_plan.weekdays == []
    assert updated_plan.target_date is None
    assert len(updated_entries) == 1
    assert updated_entries[0].title == "Tudo junto"


# --- KUBO-185: get_sections_for_entry + get_chapters_for_entry (compatibility) -----------


def test_get_sections_for_entry_returns_sections_in_order(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """get_sections_for_entry devolve as MaterialSection na ordem do plan_entry."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    _, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0], sids[1], sids[2]])],
    )

    sections = get_sections_for_entry(db, tenant_id=tenant_id, user_id=user_id, entry=entries[0])
    assert len(sections) == 3
    assert [str(s.id) for s in sections] == [str(sids[0]), str(sids[1]), str(sids[2])]
    # Cada seção tem título e sumário.
    assert sections[0].title
    assert sections[0].summary


def test_get_chapters_for_entry_derives_from_sections(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """get_chapters_for_entry (shim de compatibilidade) deriva capítulos das seções via FK.

    O scheduler/tutor agora chamam get_sections_for_entry (KUBO-189); este shim
    permanece para o tutor legacy e testes. Ele busca os material_chapter
    referenciados pelas seções do plan_entry (via FK material_chapter),
    deduplicando capítulos que aparecem em múltiplas seções.
    """
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    _, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0], sids[1]])],  # 2 seções do mesmo capítulo (capítulo 1)
    )

    chapters = get_chapters_for_entry(db, tenant_id=tenant_id, user_id=user_id, entry=entries[0])
    # 2 seções do capítulo 1 → 1 capítulo deduplicado.
    assert len(chapters) == 1
    assert chapters[0].title == "Capítulo 1"
    assert chapters[0].content == "Conteúdo 1."


def test_get_chapters_for_entry_deduplicates_across_sections(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Múltiplas seções do mesmo capítulo → 1 capítulo na lista (deduplicado)."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)

    _, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("L1", [sids[0], sids[1], sids[2], sids[3]])],  # 2 capítulos, 4 seções
    )

    chapters = get_chapters_for_entry(db, tenant_id=tenant_id, user_id=user_id, entry=entries[0])
    # 4 seções de 2 capítulos → 2 capítulos deduplicados.
    assert len(chapters) == 2
    assert {ch.title for ch in chapters} == {"Capítulo 1", "Capítulo 2"}


# --- KUBO-165: transição planning → draft -----------------------------------------------


def test_set_topic_state_planning_to_draft(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Transição planning → draft preserva materiais."""
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    set_topic_state(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id, state="planning")
    set_topic_state(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id, state="draft")

    updated = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert updated is not None
    assert updated.state == "draft"


# --- KUBO-166: ativação + scheduler + imutabilidade -------------------------------------


def _plan_with_entries(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> tuple[RecordID, RecordID]:
    """Cria tema + material + plano com 1 entry em estado 'planning'.

    Retorna (topic_id, plan_id).
    """
    topic_id, material_id = _topic_with_material(db, tenant_id, user_id)
    sids = _section_ids(db, tenant_id, user_id, material_id)
    set_topic_state(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id, state="planning")
    plan, entries = save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        entries=[("Lição 1", [sids[0]])],
    )
    set_plan_cadence(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id, weekdays=["mon", "wed"]
    )
    return topic_id, plan.id


def test_activate_plan_transitions_to_scheduled(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """activate_plan seta topic.state='scheduled', plan.status='active', activated_at."""
    topic_id, plan_id = _plan_with_entries(db, tenant_id, user_id)
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert topic is not None
    assert topic.state == "scheduled"

    plan, _ = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert plan is not None
    assert plan.status == "active"
    assert plan.activated_at is not None


def test_activate_plan_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro não pode ativar plano alheio."""
    topic_id, _ = _plan_with_entries(db, tenant_id, user_id)
    other = tenancy.create_user(db, firebase_uid="other-activate-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")
    with pytest.raises(StoreError):
        activate_plan(db, tenant_id=tenant_id, user_id=other.id, topic_id=topic_id)


def test_list_topics_by_state_returns_only_matching(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """list_topics_by_state filtra por estado e por owner."""
    topic_id, _ = _plan_with_entries(db, tenant_id, user_id)
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    scheduled = list_topics_by_state(db, tenant_id=tenant_id, user_id=user_id, state="scheduled")
    assert len(scheduled) == 1
    assert str(scheduled[0].id) == str(topic_id)

    running = list_topics_by_state(db, tenant_id=tenant_id, user_id=user_id, state="running")
    assert running == []


def test_create_lesson_persists_lesson(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """create_lesson cria registro de lesson para um plan_entry."""
    topic_id, plan_id = _plan_with_entries(db, tenant_id, user_id)
    _, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    from datetime import date, datetime

    lesson_date = date(2026, 8, 4)
    create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entries[0].id,
        scheduled_for=datetime(lesson_date.year, lesson_date.month, lesson_date.day),
    )
    assert count_lessons_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id) == 1


def test_create_lesson_unique_per_day(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Não pode criar duas lições para o mesmo dia (UNIQUE lesson_plan_day)."""
    topic_id, plan_id = _plan_with_entries(db, tenant_id, user_id)
    _, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    from datetime import datetime

    lesson_dt = datetime(2026, 8, 4)
    create_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        plan_entry_id=entries[0].id,
        scheduled_for=lesson_dt,
    )
    with pytest.raises(StoreError):
        create_lesson(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            plan_id=plan_id,
            plan_entry_id=entries[0].id,
            scheduled_for=lesson_dt,
        )


def test_deactivate_plan_reverts_to_planning(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """deactivate_plan: scheduled → planning, reverte status + activated_at."""
    topic_id, plan_id = _plan_with_entries(db, tenant_id, user_id)
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    deactivate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert topic is not None
    assert topic.state == "planning"

    plan, _ = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert plan is not None
    assert plan.status == "proposed"
    assert plan.activated_at is None


def test_deactivate_plan_cas_blocks_running(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """deactivate_plan CAS: se tema já está running, não reverte (CAS falha)."""
    from datetime import datetime

    topic_id, plan_id = _plan_with_entries(db, tenant_id, user_id)
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    _, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    # Simula scheduler: transiciona scheduled → running + congela plano.
    transition_to_running(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        plan_id=plan_id,
        plan_entry_id=entries[0].id,
        scheduled_for=datetime(2026, 8, 4),
    )

    # deactivate_plan não reverte (CAS AND state='scheduled' + AND status='active' falham).
    deactivate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert topic is not None
    assert topic.state == "running"  # não reverteu

    plan, _ = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert plan is not None
    assert plan.status == "running"  # transition_to_running congelou o plano
    assert plan.activated_at is not None


def test_transition_to_running_atomic(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """transition_to_running: cria 1ª lição + transiciona scheduled→running atômico."""
    from datetime import datetime

    topic_id, plan_id = _plan_with_entries(db, tenant_id, user_id)
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    _, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    transition_to_running(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        plan_id=plan_id,
        plan_entry_id=entries[0].id,
        scheduled_for=datetime(2026, 8, 4),
    )

    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    assert topic is not None
    assert topic.state == "running"
    assert count_lessons_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id) == 1


def test_transition_to_running_cas_idempotent(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """transition_to_running CAS: se já está running, não cria 2ª lição nem re-transiciona."""
    from datetime import datetime

    topic_id, plan_id = _plan_with_entries(db, tenant_id, user_id)
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    _, entries = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)

    transition_to_running(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        plan_id=plan_id,
        plan_entry_id=entries[0].id,
        scheduled_for=datetime(2026, 8, 4),
    )
    # 2ª chamada: CAS AND state='scheduled' falha → não cria 2ª lição.
    transition_to_running(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        plan_id=plan_id,
        plan_entry_id=entries[0].id,
        scheduled_for=datetime(2026, 8, 4),
    )
    assert count_lessons_for_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id) == 1
