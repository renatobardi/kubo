"""Store de Tema e Plano (ADR-0043, KUBO-136), user-scoped no tenant.

Integração (SurrealDB real), banco próprio `test_study_plan`. Dois eixos que só o banco
prova: ATOMICIDADE (proposta é tudo-ou-nada; re-sequenciar e trocar lições de lugar
esbarra em índice UNIQUE) e ISOLAMENTO entre USUÁRIOS do mesmo tenant — plano de estudo é
dado pessoal, e um teste que só cortasse por tenant deixaria passar o plano do colega.

A máquina de estados é comportamento, não detalhe: só `proposed` é editável, e a ativação
congela a data-alvo. Editar plano ativo apagaria o compromisso que o dono já assumiu.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import MembershipRequiredError, StoreError
from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    PlanEntryInput,
    activate_plan,
    create_material,
    create_topic,
    get_plan_for_topic,
    get_topic,
    get_topic_for_material,
    list_plan_entries,
    list_topics,
    move_plan_entry,
    remove_plan_entry,
    save_plan_proposal,
    set_plan_cadence,
)
from kubo.study.parsing import ParsedChapter

pytestmark = pytest.mark.integration

_PLAN_DB = "test_study_plan"

_WEEK = ["mon", "tue", "wed", "thu", "fri"]
_TARGET = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
_OTHER_TARGET = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_PLAN_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_PLAN_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_PLAN_DB};")


def _material(db: Any, tenant_id: RecordID, user_id: RecordID, *, chapters: int = 4) -> Any:
    """Material com N capítulos — a matéria-prima do plano."""
    return create_material(
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
            for i in range(1, chapters + 1)
        ],
    )


def _chapter_ids(db: Any, material_id: RecordID) -> list[RecordID]:
    """Ids dos capítulos do material, na ordem de leitura."""
    rows = db.query(
        "SELECT * FROM material_chapter WHERE material = $m ORDER BY seq;", {"m": material_id}
    )
    return [row["id"] for row in rows]


def _topic(db: Any, tenant_id: RecordID, user_id: RecordID) -> Any:
    """Material + tema, o par que toda cena precisa."""
    material = _material(db, tenant_id, user_id)
    return create_topic(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material.id,
        title=material.title,
    )


def _entries(chapter_ids: list[RecordID], count: int = 3) -> list[PlanEntryInput]:
    """`count` lições, uma por capítulo, `seq` 1-based."""
    return [
        PlanEntryInput(seq=i + 1, title=f"Aula {i + 1}", chapter_ids=[chapter_ids[i]])
        for i in range(count)
    ]


def _propose(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    entries: list[PlanEntryInput],
    *,
    target: datetime | None = _TARGET,
) -> Any:
    return save_plan_proposal(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_id,
        weekdays=_WEEK,
        target_date=target,
        entries=entries,
    )


def _other_member(db: Any, tenant_id: RecordID) -> RecordID:
    """Segundo usuário, MEMBRO DO MESMO TENANT — o vizinho que não pode ver o plano."""
    other = tenancy.create_user(db, firebase_uid="other-user-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")
    return other.id


def test_create_topic_persists_and_is_readable(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O tema nasce ligado ao material, com o dono e o tenant carimbados."""
    material = _material(db, tenant_id, user_id)

    topic = create_topic(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material.id, title="Manual de Kubo"
    )

    assert topic.material == material.id
    assert topic.user_id == user_id
    assert topic.tenant_id == tenant_id
    fetched = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert fetched is not None
    assert fetched.title == "Manual de Kubo"
    assert [t.id for t in list_topics(db, tenant_id=tenant_id, user_id=user_id)] == [topic.id]


def test_material_has_at_most_one_topic(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Segundo tema no mesmo material é recusado — 1 material = 1 tema."""
    material = _material(db, tenant_id, user_id)
    create_topic(db, tenant_id=tenant_id, user_id=user_id, material_id=material.id, title="Um")

    with pytest.raises(StoreError):
        create_topic(
            db, tenant_id=tenant_id, user_id=user_id, material_id=material.id, title="Dois"
        )

    found = get_topic_for_material(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material.id
    )
    assert found is not None
    assert found.title == "Um"


def test_create_topic_on_someone_elses_material_fails(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Material de outro membro é INEXISTENTE daqui — não dá para criar tema sobre ele."""
    material = _material(db, tenant_id, user_id)
    other_id = _other_member(db, tenant_id)

    with pytest.raises(StoreError):
        create_topic(
            db, tenant_id=tenant_id, user_id=other_id, material_id=material.id, title="Roubado"
        )


def test_topic_is_invisible_to_another_member_of_same_tenant(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O corte é por USUÁRIO: o vizinho não lista nem lê o tema."""
    topic = _topic(db, tenant_id, user_id)
    other_id = _other_member(db, tenant_id)

    assert list_topics(db, tenant_id=tenant_id, user_id=other_id) == []
    assert get_topic(db, tenant_id=tenant_id, user_id=other_id, topic_id=topic.id) is None


def test_save_plan_proposal_persists_plan_and_entries(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A proposta nasce `proposed`, com cadência, data-alvo e lições ordenadas."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)

    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))

    assert plan.status == "proposed"
    assert plan.weekdays == _WEEK
    assert plan.target_date == _TARGET
    assert plan.activated_at is None
    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert [e.seq for e in entries] == [1, 2, 3]
    assert entries[0].chapters == [ids[0]]


def test_save_plan_proposal_is_atomic_on_duplicate_seq(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """`seq` repetido viola o UNIQUE e NADA persiste — sem plano fantasma sem lições."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    duplicated = [
        PlanEntryInput(seq=1, title="Aula 1", chapter_ids=[ids[0]]),
        PlanEntryInput(seq=1, title="Aula 1 de novo", chapter_ids=[ids[1]]),
    ]

    with pytest.raises(StoreError):
        _propose(db, tenant_id, user_id, topic.id, duplicated)

    assert get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id) is None
    assert db.query("SELECT * FROM plan_entry;") == []


def test_save_plan_proposal_replaces_the_previous_proposal(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Propor de novo RECOMEÇA a curadoria: nada da proposta anterior sobrevive."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    first = _propose(db, tenant_id, user_id, topic.id, _entries(ids, 3))

    second = _propose(db, tenant_id, user_id, topic.id, _entries(ids, 2), target=_OTHER_TARGET)

    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=second.id)
    assert [e.seq for e in entries] == [1, 2]
    assert second.target_date == _OTHER_TARGET
    assert list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=first.id) == []


def test_save_plan_proposal_refuses_over_an_active_plan(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Plano ativo não é reproposto — repropor apagaria o compromisso já aprovado."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    with pytest.raises(StoreError):
        _propose(db, tenant_id, user_id, topic.id, _entries(ids, 2))

    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert [e.seq for e in entries] == [1, 2, 3]


def test_remove_plan_entry_resequences_and_updates_target(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Remover a lição do meio deixa `seq` contíguo 1..N e grava a nova data-alvo."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))

    remove_plan_entry(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id, seq=2, new_target=_OTHER_TARGET
    )

    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert [e.seq for e in entries] == [1, 2]
    assert [e.title for e in entries] == ["Aula 1", "Aula 3"]
    updated = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert updated is not None
    assert updated.target_date == _OTHER_TARGET


def test_move_plan_entry_swaps_neighbours(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Mover para cima troca de lugar com a vizinha — a troca encosta no índice UNIQUE."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))

    move_plan_entry(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id, seq=3, direction="up"
    )

    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert [e.title for e in entries] == ["Aula 1", "Aula 3", "Aula 2"]
    assert [e.seq for e in entries] == [1, 2, 3]


def test_move_plan_entry_at_the_edge_does_nothing(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Subir a primeira lição é o fim da lista, não um erro: a ordem fica igual."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))

    move_plan_entry(
        db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id, seq=1, direction="up"
    )

    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert [e.title for e in entries] == ["Aula 1", "Aula 2", "Aula 3"]


def test_set_plan_cadence_changes_weekdays_and_target(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Trocar os dias grava a cadência nova junto com a data-alvo recalculada."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))

    set_plan_cadence(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan.id,
        weekdays=["sat", "sun"],
        new_target=_OTHER_TARGET,
    )

    updated = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert updated is not None
    assert updated.weekdays == ["sat", "sun"]
    assert updated.target_date == _OTHER_TARGET


def test_activate_plan_freezes_target_and_stamps_activation(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Ativar move para `active`, carimba `activated_at` e mantém a data-alvo aprovada."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))

    active = activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    assert active.status == "active"
    assert active.activated_at is not None
    assert active.target_date == _TARGET


def test_activate_plan_twice_is_an_error(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Ativar o que já está ativo é erro legível — o duplo clique não reinicia a meta."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))
    first = activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    with pytest.raises(StoreError):
        activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)

    again = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert again is not None
    assert again.activated_at == first.activated_at


def test_active_plan_is_not_editable(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Plano ativo recusa remover/mover/mudar cadência — a edição é da fase de proposta."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))
    activate_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    scope = {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan.id}

    with pytest.raises(StoreError):
        remove_plan_entry(db, seq=2, new_target=_OTHER_TARGET, **scope)
    with pytest.raises(StoreError):
        move_plan_entry(db, seq=2, direction="up", **scope)
    with pytest.raises(StoreError):
        set_plan_cadence(db, weekdays=["sat"], new_target=_OTHER_TARGET, **scope)

    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert [e.title for e in entries] == ["Aula 1", "Aula 2", "Aula 3"]


def test_plan_is_invisible_and_uneditable_to_another_member(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O vizinho de tenant não lê o plano nem as lições, e não consegue editá-las."""
    topic = _topic(db, tenant_id, user_id)
    ids = _chapter_ids(db, topic.material)
    plan = _propose(db, tenant_id, user_id, topic.id, _entries(ids))
    other_id = _other_member(db, tenant_id)

    assert get_plan_for_topic(db, tenant_id=tenant_id, user_id=other_id, topic_id=topic.id) is None
    assert list_plan_entries(db, tenant_id=tenant_id, user_id=other_id, plan_id=plan.id) == []
    with pytest.raises(StoreError):
        remove_plan_entry(
            db,
            tenant_id=tenant_id,
            user_id=other_id,
            plan_id=plan.id,
            seq=1,
            new_target=_OTHER_TARGET,
        )

    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id)
    assert [e.seq for e in entries] == [1, 2, 3]


def test_plan_store_requires_membership(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Contrato KUBO-123: sem membership no tenant, nem leitura acontece."""
    topic = _topic(db, tenant_id, user_id)
    stranger = tenancy.create_user(db, firebase_uid="stranger-uid").id

    with pytest.raises(MembershipRequiredError):
        get_topic(db, tenant_id=tenant_id, user_id=stranger, topic_id=topic.id)
