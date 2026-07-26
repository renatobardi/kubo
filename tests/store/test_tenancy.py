"""Contrato da store de tenancy (ADR-0039, KUBO-113).

Integração (SurrealDB real): tenant, user, membership e a primitiva de autorização.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.errors import (
    DuplicateMembershipError,
    DuplicateOwnerError,
    DuplicateUserError,
    InvalidRoleError,
    MembershipRequiredError,
)
from kubo.store import client, migrations, tenancy

pytestmark = pytest.mark.integration


_TENANCY_DB = "test_tenancy"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_TENANCY_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_TENANCY_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_TENANCY_DB};")


def test_create_user_and_tenant_and_owner_membership(db: Any) -> None:
    """Criar tenant com owner cria user + tenant + membership(owner) atômico."""
    user = tenancy.create_user(db, firebase_uid="uid-1", email="a@example.com")
    tenant = tenancy.create_tenant(db, name="Equipe A", owner_user_id=user.id)

    assert tenant.name == "Equipe A"
    loaded = tenancy.get_tenant(db, tenant.id)
    assert loaded is not None
    assert loaded.name == "Equipe A"

    memberships = tenancy.list_memberships_for_tenant(db, tenant.id)
    assert len(memberships) == 1
    assert memberships[0].user == user.id
    assert memberships[0].tenant == tenant.id
    assert memberships[0].role == "owner"


def test_get_user_by_firebase_uid(db: Any) -> None:
    """Busca por firebase_uid devolve o user correto."""
    user = tenancy.create_user(db, firebase_uid="uid-lookup", email="lookup@example.com")
    found = tenancy.get_user_by_firebase_uid(db, "uid-lookup")
    assert found is not None
    assert found.id == user.id
    assert found.firebase_uid == "uid-lookup"
    assert found.email == "lookup@example.com"


def test_get_user_by_firebase_uid_missing_returns_none(db: Any) -> None:
    """firebase_uid inexistente retorna None."""
    assert tenancy.get_user_by_firebase_uid(db, "não-existe") is None


def test_duplicate_firebase_uid_is_refused(db: Any) -> None:
    """Criar user com firebase_uid repetido recusa."""
    tenancy.create_user(db, firebase_uid="uid-dup", email="a@example.com")

    with pytest.raises(DuplicateUserError):
        tenancy.create_user(db, firebase_uid="uid-dup", email="b@example.com")


def test_user_can_belong_to_multiple_tenants(db: Any) -> None:
    """Um user pode ser owner de um tenant e member de outro."""
    user = tenancy.create_user(db, firebase_uid="uid-multi", email="multi@example.com")
    owner_b = tenancy.create_user(db, firebase_uid="uid-owner-b", email="owner-b@example.com")
    tenant_a = tenancy.create_tenant(db, name="A", owner_user_id=user.id)
    tenant_b = tenancy.create_tenant(db, name="B", owner_user_id=owner_b.id)

    tenancy.create_membership(db, user_id=user.id, tenant_id=tenant_b.id, role="member")

    memberships = tenancy.list_memberships_for_user(db, user.id)
    assert len(memberships) == 2  # owner A + member B
    by_role = {(str(m.tenant), m.role) for m in memberships}
    assert (str(tenant_a.id), "owner") in by_role
    assert (str(tenant_b.id), "member") in by_role


def test_second_owner_membership_is_refused(db: Any) -> None:
    """Criar uma segunda membership de owner no mesmo tenant recusa."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner", email="owner@example.com")
    other = tenancy.create_user(db, firebase_uid="uid-other", email="other@example.com")
    tenant = tenancy.create_tenant(db, name="Protegido", owner_user_id=owner.id)

    with pytest.raises(DuplicateOwnerError):
        tenancy.create_membership(db, user_id=other.id, tenant_id=tenant.id, role="owner")


def test_member_is_allowed_after_owner_exists(db: Any) -> None:
    """Após haver um owner, membros adicionais são permitidos."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-2", email="owner2@example.com")
    member = tenancy.create_user(db, firebase_uid="uid-member", email="member@example.com")
    tenant = tenancy.create_tenant(db, name="Colaborativo", owner_user_id=owner.id)

    membership = tenancy.create_membership(
        db, user_id=member.id, tenant_id=tenant.id, role="member"
    )
    assert membership.role == "member"
    assert membership.user == member.id
    assert membership.tenant == tenant.id


def test_duplicate_membership_is_refused(db: Any) -> None:
    """Um user não pode ter duas memberships no mesmo tenant."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-3", email="owner3@example.com")
    tenant = tenancy.create_tenant(db, name="Sem-duplicata", owner_user_id=owner.id)

    # owner tentando virar member do próprio tenant.
    with pytest.raises(DuplicateMembershipError):
        tenancy.create_membership(db, user_id=owner.id, tenant_id=tenant.id, role="member")


def test_invalid_role_is_refused(db: Any) -> None:
    """Papel de membership fora de owner/member recusa."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-4", email="owner4@example.com")
    member = tenancy.create_user(db, firebase_uid="uid-member-2", email="member2@example.com")
    tenant = tenancy.create_tenant(db, name="Papel-fixo", owner_user_id=owner.id)

    with pytest.raises(InvalidRoleError):
        tenancy.create_membership(
            db,
            user_id=member.id,
            tenant_id=tenant.id,
            role="admin",  # type: ignore[reportArgumentType]
        )


def test_assert_membership_accepts_member(db: Any) -> None:
    """Primitiva de autorização passa quando user é member do tenant."""
    owner = tenancy.create_user(db, firebase_uid="uid-ok", email="ok@example.com")
    tenant = tenancy.create_tenant(db, name="Ok", owner_user_id=owner.id)

    # Não levanta.
    tenancy.assert_membership(db, user_id=owner.id, tenant_id=tenant.id)


def test_assert_membership_rejects_non_member(db: Any) -> None:
    """Primitiva de autorização recusa user que não pertence ao tenant."""
    user = tenancy.create_user(db, firebase_uid="uid-out", email="out@example.com")
    other = tenancy.create_user(db, firebase_uid="uid-other-3", email="other3@example.com")
    tenant = tenancy.create_tenant(db, name="Fechado", owner_user_id=other.id)

    with pytest.raises(MembershipRequiredError):
        tenancy.assert_membership(db, user_id=user.id, tenant_id=tenant.id)


def test_tenant_isolation_via_membership(db: Any) -> None:
    """Dois tenants e dois users: cada user só passa no assert do próprio tenant."""
    user_a = tenancy.create_user(db, firebase_uid="uid-a", email="a@example.com")
    user_b = tenancy.create_user(db, firebase_uid="uid-b", email="b@example.com")
    tenant_a = tenancy.create_tenant(db, name="Tenant A", owner_user_id=user_a.id)
    tenant_b = tenancy.create_tenant(db, name="Tenant B", owner_user_id=user_b.id)

    tenancy.assert_membership(db, user_id=user_a.id, tenant_id=tenant_a.id)
    tenancy.assert_membership(db, user_id=user_b.id, tenant_id=tenant_b.id)

    with pytest.raises(MembershipRequiredError):
        tenancy.assert_membership(db, user_id=user_a.id, tenant_id=tenant_b.id)
    with pytest.raises(MembershipRequiredError):
        tenancy.assert_membership(db, user_id=user_b.id, tenant_id=tenant_a.id)


def test_is_superadmin_checks_allowlist() -> None:
    """is_superadmin é True só para UIDs na allowlist."""
    assert tenancy.is_superadmin("admin-uid", {"admin-uid"})
    assert not tenancy.is_superadmin("other-uid", {"admin-uid"})


def test_assert_membership_or_superadmin_bypasses_for_superadmin(db: Any) -> None:
    """superadmin passa no assert mesmo sem membership no tenant."""
    user = tenancy.create_user(db, firebase_uid="uid-admin", email="admin@example.com")
    other = tenancy.create_user(db, firebase_uid="uid-other", email="other@example.com")
    tenant = tenancy.create_tenant(db, name="Protegido", owner_user_id=other.id)

    # Sem superadmin, recusa.
    with pytest.raises(MembershipRequiredError):
        tenancy.assert_membership_or_superadmin(
            db, user_id=user.id, tenant_id=tenant.id, is_superadmin=False
        )

    # Com superadmin, passa.
    tenancy.assert_membership_or_superadmin(
        db, user_id=user.id, tenant_id=tenant.id, is_superadmin=True
    )


def test_get_or_create_user_and_tenant_creates_on_first_login(db: Any) -> None:
    """Primeiro login de uma identidade Firebase cria user + tenant + owner membership."""
    user, tenant = tenancy.get_or_create_user_and_tenant(
        db, firebase_uid="uid-signup", email="signup@example.com"
    )

    assert user.firebase_uid == "uid-signup"
    assert user.email == "signup@example.com"
    assert tenant.name == "signup@example.com"

    memberships = tenancy.list_memberships_for_user(db, user.id)
    assert len(memberships) == 1
    assert memberships[0].role == "owner"
    assert memberships[0].tenant == tenant.id


def test_get_or_create_user_and_tenant_reuses_existing_user(db: Any) -> None:
    """Segundo login da mesma identidade retorna o user/tenant existentes."""
    first_user, first_tenant = tenancy.get_or_create_user_and_tenant(
        db, firebase_uid="uid-returning", email="returning@example.com"
    )

    second_user, second_tenant = tenancy.get_or_create_user_and_tenant(
        db, firebase_uid="uid-returning", email="other@example.com"
    )

    assert second_user.id == first_user.id
    assert second_tenant.id == first_tenant.id
    # E-mail não é atualizado no retorno (identidade externa estável).
    assert second_user.email == "returning@example.com"
