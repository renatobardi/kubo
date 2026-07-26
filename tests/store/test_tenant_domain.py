"""Contrato de scoping por tenant nos domínios centrais (KUBO-117).

Integração (SurrealDB real): flow/task/deliverable/persona e distilled/entity
só são legíveis/escritáveis dentro do tenant do usuário.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import MembershipRequiredError
from kubo.runtime.personas import load_personas_from_dir
from kubo.store import client, migrations, tenancy
from kubo.store.flows import instantiate_flow, list_flows
from kubo.store.knowledge import get_or_create_entity, list_entities

pytestmark = pytest.mark.integration

_TENANT_DOMAIN_DB = "test_tenant_domain"
_PERSONAS = load_personas_from_dir(Path(__file__).parents[2] / "catalogs" / "personas")


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_TENANT_DOMAIN_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_TENANT_DOMAIN_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_TENANT_DOMAIN_DB};")


def _tenant_owner(db: Any, *, firebase_uid: str, name: str = "Team") -> tuple[Any, Any]:
    user = tenancy.create_user(db, firebase_uid=firebase_uid)
    tenant = tenancy.create_tenant(db, name=name, owner_user_id=user.id)
    return user, tenant


def _instantiate(db: Any, tenant_id: RecordID, user_id: RecordID) -> Any:
    from kubo.runtime.flow_templates import load_flow_template

    template = load_flow_template(
        Path(__file__).parents[2] / "catalogs" / "flow_templates" / "analysis.yaml"
    )
    return instantiate_flow(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        template=template,
        personas=_PERSONAS,
        question="q?",
    )


def test_flow_is_tenant_scoped(db: Any) -> None:
    """Flow criado num tenant só é listável dentro desse tenant."""
    owner_a, tenant_a = _tenant_owner(db, firebase_uid="uid-a")
    owner_b, tenant_b = _tenant_owner(db, firebase_uid="uid-b")

    inst = _instantiate(db, tenant_a.id, owner_a.id)

    flows_a = list_flows(db, tenant_id=tenant_a.id, user_id=owner_a.id, limit=20, start=0)
    assert len(flows_a) == 1
    assert flows_a[0].id == str(inst.flow)

    with pytest.raises(MembershipRequiredError):
        list_flows(db, tenant_id=tenant_a.id, user_id=owner_b.id, limit=20, start=0)


def test_entity_is_tenant_scoped(db: Any) -> None:
    """Entity criada num tenant só aparece na listagem desse tenant; mesmo nome em
    tenants distintos vive em records isolados."""
    owner_a, tenant_a = _tenant_owner(db, firebase_uid="uid-entity-a")
    owner_b, tenant_b = _tenant_owner(db, firebase_uid="uid-entity-b")

    get_or_create_entity(db, tenant_id=tenant_a.id, user_id=owner_a.id, name="Rust")
    get_or_create_entity(db, tenant_id=tenant_b.id, user_id=owner_b.id, name="Rust")

    entities_a = list_entities(db, tenant_id=tenant_a.id, user_id=owner_a.id, limit=20, start=0)
    assert len(entities_a) == 1
    assert entities_a[0].name == "Rust"

    entities_b = list_entities(db, tenant_id=tenant_b.id, user_id=owner_b.id, limit=20, start=0)
    assert len(entities_b) == 1
    assert entities_b[0].name == "Rust"

    with pytest.raises(MembershipRequiredError):
        list_entities(db, tenant_id=tenant_a.id, user_id=owner_b.id, limit=20, start=0)
