"""Contrato de tenancy obrigatório nos domínios centrais (KUBO-123).

Integração (SurrealDB real): toda função pública de flows.py recebe uma
`ScopedStore` que carrega `(tenant_id, user_id)`, checa membership na criação
e injeta `$tenant_id`/`$user_id` em toda query. knowledge.py segue com
`tenant_id`/`user_id` explícitos (migração pendente).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import MembershipRequiredError
from kubo.runtime.flow_templates import load_flow_template
from kubo.runtime.personas import load_personas_from_dir
from kubo.store import client, migrations, tenancy
from kubo.store.flows import (
    count_flows,
    create_task,
    insert_deliverable,
    instantiate_flow,
    list_flows,
)
from kubo.store.knowledge import Chunk, get_or_create_entity, insert_distilled, list_entities
from kubo.store.scoped import scoped

pytestmark = pytest.mark.integration

_TENANT_CONTRACT_DB = "test_tenant_contract"
_PERSONAS = load_personas_from_dir(Path(__file__).parents[2] / "catalogs" / "personas")


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_TENANT_CONTRACT_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_TENANT_CONTRACT_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_TENANT_CONTRACT_DB};")


def _tenant_owner(db: Any, *, firebase_uid: str, name: str = "Team") -> tuple[Any, Any]:
    user = tenancy.create_user(db, firebase_uid=firebase_uid)
    tenant = tenancy.create_tenant(db, name=name, owner_user_id=user.id)
    return user, tenant


def _analysis_template() -> Any:
    return load_flow_template(
        Path(__file__).parents[2] / "catalogs" / "flow_templates" / "analysis.yaml"
    )


def test_instantiate_flow_rejects_user_from_other_tenant(db: Any) -> None:
    """scoped() com user que não pertence ao tenant levanta MembershipRequiredError
    na criação da sessão — antes de qualquer query de flows."""
    owner_a, tenant_a = _tenant_owner(db, firebase_uid="uid-a")
    owner_b, _ = _tenant_owner(db, firebase_uid="uid-b")
    with pytest.raises(MembershipRequiredError):
        scoped(db, tenant_id=tenant_a.id, user_id=owner_b.id)


def test_instantiate_flow_persists_tenant_id(db: Any) -> None:
    """instantiate_flow grava tenant_id no flow e nas personas materializadas."""
    owner, tenant = _tenant_owner(db, firebase_uid="uid-owner")
    template = _analysis_template()
    session = scoped(db, tenant_id=tenant.id, user_id=owner.id)
    inst = instantiate_flow(
        session,
        template=template,
        personas=_PERSONAS,
        question="q?",
    )
    flow = db.query("SELECT * FROM $f;", {"f": inst.flow})[0]
    assert flow["tenant_id"] == tenant.id
    for persona_id in inst.personas.values():
        persona = db.query("SELECT * FROM $p;", {"p": persona_id})[0]
        assert persona["tenant_id"] == tenant.id


def _analysis_inst(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> tuple[RecordID, RecordID, RecordID]:
    """Instancia analysis e retorna (flow, analyst_persona, human_persona)."""
    session = scoped(db, tenant_id=tenant_id, user_id=user_id)
    inst = instantiate_flow(
        session,
        template=_analysis_template(),
        personas=_PERSONAS,
        question="q?",
    )
    return inst.flow, inst.personas["analista"], inst.personas["humano"]


def test_create_task_persists_tenant_id(db: Any) -> None:
    """create_task grava tenant_id no task e arestas (scoped pela sessão)."""
    owner, tenant = _tenant_owner(db, firebase_uid="uid-task")
    flow, analyst, _ = _analysis_inst(db, tenant.id, owner.id)
    session = scoped(db, tenant_id=tenant.id, user_id=owner.id)

    task = create_task(
        session,
        flow=flow,
        persona=analyst,
        state="created",
    )
    row = db.query("SELECT * FROM $t;", {"t": task})[0]
    assert row["tenant_id"] == tenant.id
    belongs = db.query("SELECT * FROM belongs_to WHERE in = $t;", {"t": task})
    assert belongs[0]["tenant_id"] == tenant.id


def test_list_flows_filters_by_tenant(db: Any) -> None:
    """list_flows só devolve flows do tenant da sessão."""
    owner_a, tenant_a = _tenant_owner(db, firebase_uid="uid-list-a")
    owner_b, tenant_b = _tenant_owner(db, firebase_uid="uid-list-b")
    _analysis_inst(db, tenant_a.id, owner_a.id)
    _analysis_inst(db, tenant_b.id, owner_b.id)

    session_a = scoped(db, tenant_id=tenant_a.id, user_id=owner_a.id)
    flows_a = list_flows(session_a, limit=10, start=0)
    assert len(flows_a) == 1
    assert count_flows(session_a) == 1

    with pytest.raises(MembershipRequiredError):
        scoped(db, tenant_id=tenant_a.id, user_id=owner_b.id)


def test_insert_deliverable_persists_tenant_id(db: Any) -> None:
    """insert_deliverable grava tenant_id no deliverable/arestas (scoped pela sessão)."""
    owner, tenant = _tenant_owner(db, firebase_uid="uid-del")
    flow, analyst, _ = _analysis_inst(db, tenant.id, owner.id)
    session = scoped(db, tenant_id=tenant.id, user_id=owner.id)
    task = create_task(
        session,
        flow=flow,
        persona=analyst,
        state="created",
    )

    deliverable = insert_deliverable(
        session,
        flow=flow,
        task=task,
        kind="report",
        content="c",
        consulted=[],
    )
    row = db.query("SELECT * FROM $d;", {"d": deliverable})[0]
    assert row["tenant_id"] == tenant.id
    produces = db.query(
        "SELECT * FROM produces WHERE in = $f AND out = $d;", {"f": flow, "d": deliverable}
    )
    assert produces[0]["tenant_id"] == tenant.id


def _sample_chunk() -> Chunk:
    return Chunk(
        text="t",
        seq=0,
        embedding=[0.0] * 768,
        model="m",
        dim=768,
        task_type="search",
    )


def test_entity_and_distilled_require_tenant(db: Any) -> None:
    """get_or_create_entity, insert_distilled e list_entities exigem/filtram tenant."""
    owner_a, tenant_a = _tenant_owner(db, firebase_uid="uid-ent-a")
    owner_b, tenant_b = _tenant_owner(db, firebase_uid="uid-ent-b")

    session_a = scoped(db, tenant_id=tenant_a.id, user_id=owner_a.id)
    session_b = scoped(db, tenant_id=tenant_b.id, user_id=owner_b.id)

    get_or_create_entity(session_a, name="Rust")
    get_or_create_entity(session_b, name="Rust")

    entities_a = list_entities(session_a, limit=10, start=0)
    assert len(entities_a) == 1

    with pytest.raises(MembershipRequiredError):
        scoped(db, tenant_id=tenant_a.id, user_id=owner_b.id)

    source = db.query(
        "CREATE source SET tenant_id=$t, kind='rss', canonical='c', title='t'",
        {"t": tenant_a.id},
    )[0]["id"]
    item = db.query("CREATE item SET external_id='x', content='c';")[0]["id"]
    db.query("RELATE $i->from_source->$s;", {"i": item, "s": source})

    distilled = insert_distilled(
        session_a,
        item=item,
        summary="s",
        chunks=[_sample_chunk()],
    )
    row = db.query("SELECT * FROM $d;", {"d": distilled})[0]
    assert row["tenant_id"] == tenant_a.id
