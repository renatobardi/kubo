"""Contrato da store de credenciais de tenant (ADR-0039 §IV, KUBO-115).

Integração (SurrealDB real): cifragem, rotação, revogação e isolamento.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.errors import ConfigError, MembershipRequiredError
from kubo.store import client, migrations, tenancy, tenant_credentials

pytestmark = pytest.mark.integration

_CREDENTIALS_DB = "test_tenant_credentials"


def _key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    monkeypatch.setenv("KUBO_TENANT_CREDENTIAL_KEY", _key())
    cfg = replace(client.config(), database=_CREDENTIALS_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_CREDENTIALS_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_CREDENTIALS_DB};")
    # Limpa a env para não vazar entre testes.
    monkeypatch.delenv("KUBO_TENANT_CREDENTIAL_KEY", raising=False)


def _owner_and_tenant(db: Any) -> tuple[Any, Any]:
    user = tenancy.create_user(db, firebase_uid="uid-cred", email="cred@example.com")
    tenant = tenancy.create_tenant(db, name="Cred Tenant", owner_user_id=user.id)
    return user, tenant


def test_set_and_get_credential_roundtrip(db: Any) -> None:
    """Segredo é recuperado em claro após ser cifrado em repouso."""
    user, tenant = _owner_and_tenant(db)

    tenant_credentials.set_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai", secret="sk-tenant-key"
    )
    loaded = tenant_credentials.get_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai"
    )

    assert loaded == "sk-tenant-key"


def test_credential_is_encrypted_in_database(db: Any) -> None:
    """O segredo nunca aparece em claro no dump da tabela."""
    user, tenant = _owner_and_tenant(db)

    tenant_credentials.set_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai", secret="sk-secret"
    )
    rows = db.query(
        "SELECT * FROM tenant_credential WHERE provider = $provider;",
        {"provider": "openai"},
    )

    assert len(rows) == 1
    assert "sk-secret" not in rows[0]["value"]
    assert rows[0]["value"].startswith("gAAAA")  # prefixo Fernet


def test_rotate_credential(db: Any) -> None:
    """Sobrescrever uma credencial existente atualiza o valor."""
    user, tenant = _owner_and_tenant(db)

    tenant_credentials.set_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai", secret="old-key"
    )
    tenant_credentials.set_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai", secret="new-key"
    )
    loaded = tenant_credentials.get_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai"
    )

    assert loaded == "new-key"


def test_revoke_credential(db: Any) -> None:
    """Apagar uma credencial a remove por provider."""
    user, tenant = _owner_and_tenant(db)

    tenant_credentials.set_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai", secret="key"
    )
    tenant_credentials.delete_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai"
    )
    loaded = tenant_credentials.get_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai"
    )

    assert loaded is None


def test_get_missing_credential_returns_none(db: Any) -> None:
    """Provider não cadastrado devolve None."""
    user, tenant = _owner_and_tenant(db)

    loaded = tenant_credentials.get_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="anthropic"
    )
    assert loaded is None


def test_credential_isolation_between_tenants(db: Any) -> None:
    """Um tenant não lê a credencial de outro tenant."""
    owner_a = tenancy.create_user(db, firebase_uid="uid-a", email="a@example.com")
    tenant_a = tenancy.create_tenant(db, name="A", owner_user_id=owner_a.id)
    owner_b = tenancy.create_user(db, firebase_uid="uid-b", email="b@example.com")
    tenant_b = tenancy.create_tenant(db, name="B", owner_user_id=owner_b.id)

    tenant_credentials.set_credential(
        db, user_id=owner_a.id, tenant_id=tenant_a.id, provider="openai", secret="key-a"
    )

    assert (
        tenant_credentials.get_credential(
            db, user_id=owner_a.id, tenant_id=tenant_a.id, provider="openai"
        )
        == "key-a"
    )
    assert (
        tenant_credentials.get_credential(
            db, user_id=owner_b.id, tenant_id=tenant_b.id, provider="openai"
        )
        is None
    )


def test_member_of_other_tenant_cannot_read(db: Any) -> None:
    """Usuário sem membership no tenant é recusado na leitura."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner", email="owner@example.com")
    tenant = tenancy.create_tenant(db, name="Protegido", owner_user_id=owner.id)
    other = tenancy.create_user(db, firebase_uid="uid-other", email="other@example.com")

    tenant_credentials.set_credential(
        db, user_id=owner.id, tenant_id=tenant.id, provider="openai", secret="key"
    )

    with pytest.raises(MembershipRequiredError):
        tenant_credentials.get_credential(
            db, user_id=other.id, tenant_id=tenant.id, provider="openai"
        )


def test_set_credential_fails_without_key_env(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem a chave mestra, set_credential falha fechado com ConfigError."""
    monkeypatch.delenv("KUBO_TENANT_CREDENTIAL_KEY", raising=False)
    user, tenant = _owner_and_tenant(db)

    with pytest.raises(ConfigError):
        tenant_credentials.set_credential(
            db, user_id=user.id, tenant_id=tenant.id, provider="openai", secret="key"
        )


def test_get_credential_fails_without_key_env(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem a chave mestra, get_credential falha fechado com ConfigError."""
    user, tenant = _owner_and_tenant(db)
    tenant_credentials.set_credential(
        db, user_id=user.id, tenant_id=tenant.id, provider="openai", secret="key"
    )
    monkeypatch.delenv("KUBO_TENANT_CREDENTIAL_KEY", raising=False)

    with pytest.raises(ConfigError):
        tenant_credentials.get_credential(
            db, user_id=user.id, tenant_id=tenant.id, provider="openai"
        )
