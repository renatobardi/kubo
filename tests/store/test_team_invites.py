"""Contract tests for team_invite store (ADR-0041, KUBO-120).

Integration (real SurrealDB): team_invite and acceptance becoming a member membership.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from kubo.errors import TeamInviteError
from kubo.store import client, migrations, team_invites, tenancy

pytestmark = pytest.mark.integration


_TEAM_INVITES_DB = "test_team_invites"


@pytest.fixture
def db() -> Iterator[Any]:
    """Test-only database, migrated from scratch and cleaned afterwards."""
    cfg = replace(client.config(), database=_TEAM_INVITES_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_TEAM_INVITES_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_TEAM_INVITES_DB};")


def test_create_team_invite(db: Any) -> None:
    """Owner creates a team invite with token, member role and pending status."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner", email="owner@example.com")
    tenant = tenancy.create_tenant(db, name="Equipe", owner_user_id=owner.id)

    invite = team_invites.create_team_invite(db, tenant_id=tenant.id, created_by=owner.id)

    assert invite.tenant_id == tenant.id
    assert invite.created_by == owner.id
    assert invite.role == "member"
    assert invite.status == "pending"
    assert len(invite.token) >= 16
    assert invite.expires_at > datetime.now(timezone.utc)


def test_get_team_invite_by_token(db: Any) -> None:
    """Lookup by token returns the right invite; missing token returns None."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-2", email="owner2@example.com")
    tenant = tenancy.create_tenant(db, name="Equipe", owner_user_id=owner.id)
    invite = team_invites.create_team_invite(db, tenant_id=tenant.id, created_by=owner.id)

    found = team_invites.get_team_invite_by_token(db, invite.token)
    assert found is not None
    assert found.id == invite.id
    assert team_invites.get_team_invite_by_token(db, "does-not-exist") is None


def test_accept_team_invite_creates_member(db: Any) -> None:
    """Acceptance creates a member membership in the invite's tenant and marks accepted."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-3", email="owner3@example.com")
    tenant = tenancy.create_tenant(db, name="Equipe", owner_user_id=owner.id)
    invite = team_invites.create_team_invite(db, tenant_id=tenant.id, created_by=owner.id)
    guest = tenancy.create_user(db, firebase_uid="uid-guest", email="guest@example.com")

    accepted = team_invites.accept_team_invite(db, token=invite.token, user_id=guest.id)

    assert accepted.id == invite.id
    assert accepted.status == "accepted"
    memberships = tenancy.list_memberships_for_user(db, guest.id)
    assert len(memberships) == 1
    assert memberships[0].tenant == tenant.id
    assert memberships[0].role == "member"


def test_accept_team_invite_rejects_expired(db: Any) -> None:
    """Expired invite cannot be accepted."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-4", email="owner4@example.com")
    tenant = tenancy.create_tenant(db, name="Equipe", owner_user_id=owner.id)
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    invite = team_invites.create_team_invite(
        db, tenant_id=tenant.id, created_by=owner.id, expires_at=expired
    )
    guest = tenancy.create_user(db, firebase_uid="uid-guest-2", email="guest2@example.com")

    with pytest.raises(TeamInviteError):
        team_invites.accept_team_invite(db, token=invite.token, user_id=guest.id)


def test_accept_team_invite_rejects_already_accepted(db: Any) -> None:
    """Already-accepted invite cannot be accepted again."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-5", email="owner5@example.com")
    tenant = tenancy.create_tenant(db, name="Equipe", owner_user_id=owner.id)
    invite = team_invites.create_team_invite(db, tenant_id=tenant.id, created_by=owner.id)
    guest = tenancy.create_user(db, firebase_uid="uid-guest-3", email="guest3@example.com")

    team_invites.accept_team_invite(db, token=invite.token, user_id=guest.id)
    with pytest.raises(TeamInviteError):
        team_invites.accept_team_invite(db, token=invite.token, user_id=guest.id)


def test_accept_team_invite_is_idempotent_for_existing_member(db: Any) -> None:
    """If the user is already a member, accepting only marks the invite accepted."""
    owner = tenancy.create_user(db, firebase_uid="uid-owner-7", email="owner7@example.com")
    tenant = tenancy.create_tenant(db, name="Equipe", owner_user_id=owner.id)
    guest = tenancy.create_user(db, firebase_uid="uid-guest-5", email="guest5@example.com")
    tenancy.create_membership(db, user_id=guest.id, tenant_id=tenant.id, role="member")

    invite = team_invites.create_team_invite(db, tenant_id=tenant.id, created_by=owner.id)
    accepted = team_invites.accept_team_invite(db, token=invite.token, user_id=guest.id)

    assert accepted.status == "accepted"
    memberships = tenancy.list_memberships_for_user(db, guest.id)
    assert len(memberships) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
