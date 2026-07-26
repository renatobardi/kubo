"""Contrato da store de catálogo por-tenant (ADR-0042, KUBO-119).

Integração (SurrealDB real): catálogo semeado na criação do tenant, isolamento
entre tenants, CRUD + changelog de personas/integrações/flow_templates.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.errors import MembershipRequiredError
from kubo.runtime.flow_templates import FlowTemplate
from kubo.runtime.integrations import Integration
from kubo.runtime.personas import Persona
from kubo.store import catalog, client, migrations, tenancy
from kubo.store.catalog import (
    DEFAULT_FLOW_TEMPLATES,
    DEFAULT_INTEGRATIONS,
    DEFAULT_PERSONAS,
)

pytestmark = pytest.mark.integration

_CATALOG_DB = "test_catalog"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_CATALOG_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_CATALOG_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_CATALOG_DB};")


def _tenant_owner(db: Any) -> tuple[Any, Any]:
    """Cria user + tenant; devolve (user, tenant)."""
    user = tenancy.create_user(db, firebase_uid=f"uid-{secrets.token_hex(4)}")
    tenant = tenancy.create_tenant(db, name="Cat Team", owner_user_id=user.id)
    return user, tenant


def test_create_tenant_seeds_default_catalog(db: Any) -> None:
    """Criar tenant semeia personas, integrações e flow_templates default."""
    user, tenant = _tenant_owner(db)

    personas = catalog.list_personas(db, tenant_id=tenant.id, user_id=user.id)
    names = {p["name"] for p in personas}
    assert names == {p["name"] for p in DEFAULT_PERSONAS}

    integrations = catalog.list_integrations(db, tenant_id=tenant.id, user_id=user.id)
    integ_names = {i["name"] for i in integrations}
    assert integ_names == {i["name"] for i in DEFAULT_INTEGRATIONS}

    templates = catalog.list_flow_templates(db, tenant_id=tenant.id, user_id=user.id)
    template_names = {t["name"] for t in templates}
    assert template_names == {t["name"] for t in DEFAULT_FLOW_TEMPLATES}


def test_get_persona_returns_clean_dict_for_pydantic(db: Any) -> None:
    """get_persona devolve dict que o Pydantic `Persona` valida."""
    user, tenant = _tenant_owner(db)

    row = catalog.get_persona(db, tenant_id=tenant.id, name="analista", user_id=user.id)
    assert row is not None
    persona = Persona.model_validate(row)
    assert persona.name == "analista"
    assert persona.executor == "api"
    assert "telegram" in persona.permissions


def test_upsert_persona_writes_changelog(db: Any) -> None:
    """Atualizar uma persona gera uma linha de changelog com before/after."""
    user, tenant = _tenant_owner(db)

    before_count = len(catalog.list_changelog(db, tenant_id=tenant.id, user_id=user.id))
    catalog.upsert_persona(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        persona={
            "name": "analista",
            "executor": "api",
            "model": "groq/llama-3.3-70b-versatile",
            "prompt": "prompt alterado",
            "permissions": ["telegram"],
        },
    )
    changelog = catalog.list_changelog(db, tenant_id=tenant.id, user_id=user.id)
    assert len(changelog) == before_count + 1
    entry = changelog[0]
    assert entry["kind"] == "persona"
    assert entry["item_name"] == "analista"
    assert entry["before"]["prompt"] != "prompt alterado"
    assert entry["after"]["prompt"] == "prompt alterado"
    assert entry["changed_by"] == user.id


def test_delete_persona_logs_after_none(db: Any) -> None:
    """Remover uma persona grava changelog com after=None."""
    user, tenant = _tenant_owner(db)

    catalog.delete_persona(db, tenant_id=tenant.id, name="finder", user_id=user.id)
    changelog = catalog.list_changelog(db, tenant_id=tenant.id, user_id=user.id)
    entry = next((c for c in changelog if c["item_name"] == "finder" and c["after"] is None), None)
    assert entry is not None
    assert entry["before"]["name"] == "finder"


def test_tenant_isolation_for_catalog(db: Any) -> None:
    """Um user de outro tenant não consegue ler o catálogo do tenant alvo."""
    owner_a = tenancy.create_user(db, firebase_uid=f"uid-a-{secrets.token_hex(4)}")
    tenant_a = tenancy.create_tenant(db, name="A", owner_user_id=owner_a.id)
    owner_b = tenancy.create_user(db, firebase_uid=f"uid-b-{secrets.token_hex(4)}")

    with pytest.raises(MembershipRequiredError):
        catalog.list_personas(db, tenant_id=tenant_a.id, user_id=owner_b.id)

    with pytest.raises(MembershipRequiredError):
        catalog.get_persona(db, tenant_id=tenant_a.id, name="analista", user_id=owner_b.id)


def test_get_integration_validates_pydantic(db: Any) -> None:
    """get_integration devolve dict que o Pydantic `Integration` valida."""
    user, tenant = _tenant_owner(db)

    row = catalog.get_integration(db, tenant_id=tenant.id, name="rss", user_id=user.id)
    assert row is not None
    integ = Integration.model_validate(row)
    assert integ.name == "rss"
    assert integ.auth.type == "none"


def test_get_flow_template_validates_pydantic(db: Any) -> None:
    """get_flow_template devolve dict que o Pydantic `FlowTemplate` valida."""
    user, tenant = _tenant_owner(db)

    row = catalog.get_flow_template(db, tenant_id=tenant.id, name="analysis", user_id=user.id)
    assert row is not None
    template = FlowTemplate.model_validate(row)
    assert template.name == "analysis"
    assert ["created", "analyzing"] in [list(t) for t in template.board.transitions]


def test_runtime_loaders_read_from_database(db: Any) -> None:
    """Os loaders de runtime leem do banco quando recebem tenant_id/user_id."""
    user, tenant = _tenant_owner(db)

    from kubo.runtime.flow_templates import load_flow_templates as load_flow_templates_runtime
    from kubo.runtime.integrations import load_integrations as load_integrations_runtime
    from kubo.runtime.personas import load_personas as load_personas_runtime

    personas = load_personas_runtime(db, tenant.id, user.id)
    assert "analista" in personas
    assert isinstance(personas["analista"], Persona)

    integrations = load_integrations_runtime(db, tenant.id, user.id)
    assert "rss" in integrations
    assert isinstance(integrations["rss"], Integration)

    templates = load_flow_templates_runtime(db, tenant.id, user.id)
    assert "analysis" in templates
    assert isinstance(templates["analysis"], FlowTemplate)


def test_upsert_integration_accepts_tenant_credential_ref(db: Any) -> None:
    """Integração no catálogo pode referenciar tenant_credential, além de env:VAR."""
    user, tenant = _tenant_owner(db)

    catalog.upsert_integration(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        integration={
            "name": "openai",
            "kind": "http",
            "auth": {"type": "bearer", "secret_ref": "tenant_credential:openai"},
            "rate_limit": None,
            "base_url": "https://api.openai.com",
        },
    )
    row = catalog.get_integration(db, tenant_id=tenant.id, name="openai", user_id=user.id)
    assert row is not None
    assert row["auth"]["secret_ref"] == "tenant_credential:openai"
