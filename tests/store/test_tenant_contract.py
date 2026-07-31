"""Contrato de tenancy obrigatório nos domínios centrais (KUBO-123).

Integração (SurrealDB real): toda função pública de flows.py e knowledge.py exige
`tenant_id`/`user_id` e escopa leitura/escrita no tenant.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import ConfigError, MembershipRequiredError
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


def test_instantiate_flow_requires_tenant_id_and_user_id(db: Any) -> None:
    """instantiate_flow sem tenant_id/user_id deve falhar antes de tocar o banco."""
    template = _analysis_template()
    with pytest.raises((TypeError, ConfigError)):
        instantiate_flow(  # type: ignore[reportCallIssue]
            db, template=template, personas=_PERSONAS, question="q?"
        )


def test_instantiate_flow_rejects_user_from_other_tenant(db: Any) -> None:
    """instantiate_flow com user que não pertence ao tenant levanta MembershipRequiredError."""
    owner_a, tenant_a = _tenant_owner(db, firebase_uid="uid-a")
    owner_b, _ = _tenant_owner(db, firebase_uid="uid-b")
    template = _analysis_template()
    with pytest.raises(MembershipRequiredError):
        instantiate_flow(
            db,
            tenant_id=tenant_a.id,
            user_id=owner_b.id,
            template=template,
            personas=_PERSONAS,
            question="q?",
        )


def test_instantiate_flow_persists_tenant_id(db: Any) -> None:
    """instantiate_flow grava tenant_id no flow e nas personas materializadas."""
    owner, tenant = _tenant_owner(db, firebase_uid="uid-owner")
    template = _analysis_template()
    inst = instantiate_flow(
        db,
        tenant_id=tenant.id,
        user_id=owner.id,
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
    inst = instantiate_flow(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        template=_analysis_template(),
        personas=_PERSONAS,
        question="q?",
    )
    return inst.flow, inst.personas["analista"], inst.personas["humano"]


def test_create_task_requires_tenant_and_persists_it(db: Any) -> None:
    """create_task exige tenant_id/user_id e grava tenant_id no task e arestas."""
    owner, tenant = _tenant_owner(db, firebase_uid="uid-task")
    flow, analyst, _ = _analysis_inst(db, tenant.id, owner.id)

    with pytest.raises((TypeError, ConfigError)):
        create_task(  # type: ignore[reportCallIssue]
            db, flow=flow, persona=analyst, state="created"
        )

    task = create_task(
        db,
        tenant_id=tenant.id,
        user_id=owner.id,
        flow=flow,
        persona=analyst,
        state="created",
    )
    row = db.query("SELECT * FROM $t;", {"t": task})[0]
    assert row["tenant_id"] == tenant.id
    belongs = db.query("SELECT * FROM belongs_to WHERE in = $t;", {"t": task})
    assert belongs[0]["tenant_id"] == tenant.id


def test_list_flows_filters_by_tenant(db: Any) -> None:
    """list_flows exige tenant e só devolve flows do tenant."""
    owner_a, tenant_a = _tenant_owner(db, firebase_uid="uid-list-a")
    owner_b, tenant_b = _tenant_owner(db, firebase_uid="uid-list-b")
    _analysis_inst(db, tenant_a.id, owner_a.id)
    _analysis_inst(db, tenant_b.id, owner_b.id)

    with pytest.raises((TypeError, ConfigError)):
        list_flows(db, limit=10, start=0)  # type: ignore[reportCallIssue]

    flows_a = list_flows(db, tenant_id=tenant_a.id, user_id=owner_a.id, limit=10, start=0)
    assert len(flows_a) == 1
    assert count_flows(db, tenant_id=tenant_a.id, user_id=owner_a.id) == 1

    with pytest.raises(MembershipRequiredError):
        list_flows(db, tenant_id=tenant_a.id, user_id=owner_b.id, limit=10, start=0)


def test_insert_deliverable_requires_tenant_and_persists_it(db: Any) -> None:
    """insert_deliverable exige tenant e grava tenant_id no deliverable/arestas."""
    owner, tenant = _tenant_owner(db, firebase_uid="uid-del")
    flow, analyst, _ = _analysis_inst(db, tenant.id, owner.id)
    task = create_task(
        db,
        tenant_id=tenant.id,
        user_id=owner.id,
        flow=flow,
        persona=analyst,
        state="created",
    )

    with pytest.raises((TypeError, ConfigError)):
        insert_deliverable(  # type: ignore[reportCallIssue]
            db, flow=flow, task=task, kind="report", content="c", consulted=[]
        )

    deliverable = insert_deliverable(
        db,
        tenant_id=tenant.id,
        user_id=owner.id,
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

    with pytest.raises((TypeError, ConfigError)):
        get_or_create_entity(db, name="Rust")  # type: ignore[reportCallIssue]

    get_or_create_entity(db, tenant_id=tenant_a.id, user_id=owner_a.id, name="Rust")
    get_or_create_entity(db, tenant_id=tenant_b.id, user_id=owner_b.id, name="Rust")

    entities_a = list_entities(db, tenant_id=tenant_a.id, user_id=owner_a.id, limit=10, start=0)
    assert len(entities_a) == 1

    with pytest.raises(MembershipRequiredError):
        list_entities(db, tenant_id=tenant_a.id, user_id=owner_b.id, limit=10, start=0)

    source = db.query(
        "CREATE source SET tenant_id=$t, kind='rss', canonical='c', title='t'",
        {"t": tenant_a.id},
    )[0]["id"]
    item = db.query("CREATE item SET external_id='x', content='c';")[0]["id"]
    db.query("RELATE $i->from_source->$s;", {"i": item, "s": source})

    with pytest.raises((TypeError, ConfigError)):
        insert_distilled(  # type: ignore[reportCallIssue]
            db, item=item, summary="s", chunks=[_sample_chunk()]
        )

    distilled = insert_distilled(
        db,
        tenant_id=tenant_a.id,
        user_id=owner_a.id,
        item=item,
        summary="s",
        chunks=[_sample_chunk()],
    )
    row = db.query("SELECT * FROM $d;", {"d": distilled})[0]
    assert row["tenant_id"] == tenant_a.id
