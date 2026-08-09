"""Contrato da store de catálogo por-tenant (ADR-0042, KUBO-119, ADR-0053).

Integração (SurrealDB real): catálogo semeado na criação do tenant, isolamento
entre tenants, CRUD + changelog de personas/integrações/flow_templates.
Todas as operações passam por ScopedStore (KUBO-212).
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
from kubo.store.scoped import ScopedStore, scoped

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


def _tenant_owner(db: Any) -> tuple[Any, Any, ScopedStore]:
    """Cria user + tenant; devolve (user, tenant, session escopada)."""
    user = tenancy.create_user(db, firebase_uid=f"uid-{secrets.token_hex(4)}")
    tenant = tenancy.create_tenant(db, name="Cat Team", owner_user_id=user.id)
    session = scoped(db, tenant_id=tenant.id, user_id=user.id)
    return user, tenant, session


def test_create_tenant_seeds_default_catalog(db: Any) -> None:
    """Criar tenant semeia personas, integrações e flow_templates default.

    Personas que bypassam `resolve_persona` (lidas direto de DEFAULT_PERSONAS pela
    rota) não são semeadas — apareceriam editáveis mas edições não teriam efeito
    (ADR-0046 §IV)."""
    _user, _tenant, session = _tenant_owner(db)

    personas = catalog.list_personas(session)
    names = {p["name"] for p in personas}
    expected = {
        p["name"] for p in DEFAULT_PERSONAS if p["name"] not in catalog._NON_SEEDED_PERSONAS
    }
    assert names == expected

    integrations = catalog.list_integrations(session)
    integ_names = {i["name"] for i in integrations}
    assert integ_names == {i["name"] for i in DEFAULT_INTEGRATIONS}

    templates = catalog.list_flow_templates(session)
    template_names = {t["name"] for t in templates}
    assert template_names == {t["name"] for t in DEFAULT_FLOW_TEMPLATES}


def test_get_persona_returns_clean_dict_for_pydantic(db: Any) -> None:
    """get_persona devolve dict que o Pydantic `Persona` valida."""
    _user, _tenant, session = _tenant_owner(db)

    row = catalog.get_persona(session, name="analista")
    assert row is not None
    persona = Persona.model_validate(row)
    assert persona.name == "analista"
    assert persona.executor == "api"
    assert "telegram" in persona.permissions


def test_upsert_persona_writes_changelog(db: Any) -> None:
    """Atualizar uma persona gera uma linha de changelog com before/after."""
    user, _tenant, session = _tenant_owner(db)

    before_count = len(catalog.list_changelog(session))
    catalog.upsert_persona(
        session,
        persona={
            "name": "analista",
            "executor": "api",
            "model": "groq/llama-3.3-70b-versatile",
            "prompt": "prompt alterado",
            "permissions": ["telegram"],
        },
    )
    changelog = catalog.list_changelog(session)
    assert len(changelog) == before_count + 1
    entry = changelog[0]
    assert entry["kind"] == "persona"
    assert entry["item_name"] == "analista"
    assert entry["before"]["prompt"] != "prompt alterado"
    assert entry["after"]["prompt"] == "prompt alterado"
    assert entry["changed_by"] == user.id


def test_delete_persona_logs_after_none(db: Any) -> None:
    """Remover uma persona grava changelog com after=None."""
    _user, _tenant, session = _tenant_owner(db)

    catalog.delete_persona(session, name="finder")
    changelog = catalog.list_changelog(session)
    entry = next((c for c in changelog if c["item_name"] == "finder" and c["after"] is None), None)
    assert entry is not None
    assert entry["before"]["name"] == "finder"


def test_tenant_isolation_membership_blocked(db: Any) -> None:
    """User de outro tenant não consegue criar sessão escopada para o tenant alvo.

    A barreira subiu da assinatura para a sessão: sem ScopedStore, não há como
    chamar nenhuma função de catálogo (ADR-0053 §1).
    """
    owner_a = tenancy.create_user(db, firebase_uid=f"uid-a-{secrets.token_hex(4)}")
    tenant_a = tenancy.create_tenant(db, name="A", owner_user_id=owner_a.id)
    owner_b = tenancy.create_user(db, firebase_uid=f"uid-b-{secrets.token_hex(4)}")

    with pytest.raises(MembershipRequiredError):
        scoped(db, tenant_id=tenant_a.id, user_id=owner_b.id)


def test_tenant_isolation_session_cannot_see_other_tenant(db: Any) -> None:
    """Sessão do tenant A não vê nem altera persona do tenant B (seam HTTP, KUBO-212).

    Mesmo que o caller tente usar um nome de persona que existe no tenant B,
    a sessão do tenant A não encontra — o record ID é determinístico por
    (tenant, nome) e o WHERE filtra por $tenant_id.
    """
    owner_a = tenancy.create_user(db, firebase_uid=f"uid-a-{secrets.token_hex(4)}")
    tenant_a = tenancy.create_tenant(db, name="A", owner_user_id=owner_a.id)
    owner_b = tenancy.create_user(db, firebase_uid=f"uid-b-{secrets.token_hex(4)}")
    tenant_b = tenancy.create_tenant(db, name="B", owner_user_id=owner_b.id)

    session_a = scoped(db, tenant_id=tenant_a.id, user_id=owner_a.id)
    session_b = scoped(db, tenant_id=tenant_b.id, user_id=owner_b.id)

    # Upsert da mesma persona em ambos os tenants com prompts diferentes
    catalog.upsert_persona(
        session_a,
        persona={
            "name": "shared",
            "executor": "api",
            "model": None,
            "prompt": "tenant-a-prompt",
            "permissions": [],
        },
    )
    catalog.upsert_persona(
        session_b,
        persona={
            "name": "shared",
            "executor": "api",
            "model": None,
            "prompt": "tenant-b-prompt",
            "permissions": [],
        },
    )

    # Sessão A vê só o seu prompt, não o do B
    persona_a = catalog.get_persona(session_a, name="shared")
    assert persona_a is not None
    assert persona_a["prompt"] == "tenant-a-prompt"

    persona_b = catalog.get_persona(session_b, name="shared")
    assert persona_b is not None
    assert persona_b["prompt"] == "tenant-b-prompt"

    # Sessão A lista só as suas personas
    names_a = {p["name"] for p in catalog.list_personas(session_a)}
    names_b = {p["name"] for p in catalog.list_personas(session_b)}
    assert "shared" in names_a
    assert "shared" in names_b
    # Os defaults são os mesmos, mas os registros são distintos por tenant

    # Sessão A não consegue apagar persona do tenant B
    catalog.delete_persona(session_a, name="shared")
    persona_b_after = catalog.get_persona(session_b, name="shared")
    assert persona_b_after is not None
    assert persona_b_after["prompt"] == "tenant-b-prompt"


def test_get_integration_validates_pydantic(db: Any) -> None:
    """get_integration devolve dict que o Pydantic `Integration` valida."""
    _user, _tenant, session = _tenant_owner(db)

    row = catalog.get_integration(session, name="rss")
    assert row is not None
    integ = Integration.model_validate(row)
    assert integ.name == "rss"
    assert integ.auth.type == "none"


def test_get_flow_template_validates_pydantic(db: Any) -> None:
    """get_flow_template devolve dict que o Pydantic `FlowTemplate` valida."""
    _user, _tenant, session = _tenant_owner(db)

    row = catalog.get_flow_template(session, name="analysis")
    assert row is not None
    template = FlowTemplate.model_validate(row)
    assert template.name == "analysis"
    assert ["created", "analyzing"] in [list(t) for t in template.board.transitions]


def test_runtime_loaders_read_from_database(db: Any) -> None:
    """Os loaders de runtime leem do banco quando recebem tenant_id/user_id.

    Os loaders criam a sessão internamente — o caller não muda (KUBO-212).
    """
    user, tenant, _session = _tenant_owner(db)

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
    _user, _tenant, session = _tenant_owner(db)

    catalog.upsert_integration(
        session,
        integration={
            "name": "openai",
            "kind": "http",
            "auth": {"type": "bearer", "secret_ref": "tenant_credential:openai"},
            "rate_limit": None,
            "base_url": "https://api.openai.com",
        },
    )
    row = catalog.get_integration(session, name="openai")
    assert row is not None
    assert row["auth"]["secret_ref"] == "tenant_credential:openai"
