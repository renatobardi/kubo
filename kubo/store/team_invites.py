"""Team invite (team_invite) — onboarding members into a tenant (ADR-0041, KUBO-120).

Distinct from the Telegram onboarding invite (`kubo/store/invites.py`): high-entropy
token, scoped to one tenant, with status `pending`/`accepted`/`expired`/`revoked`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

from surrealdb import RecordID

from kubo.errors import StoreError, TeamInviteError
from kubo.store import tenancy


@dataclass(frozen=True)
class TeamInvite:
    """Pending/accepted/expired/revoked team invite.

    `token` is routing PII — `repr=False` prevents leakage in logs/tracebacks.
    """

    id: RecordID
    tenant_id: RecordID
    token: str = field(repr=False)
    role: str
    created_by: RecordID
    expires_at: datetime
    status: str
    created_at: datetime


def _as_datetime(value: Any) -> datetime:
    """Normalize a SurrealDB datetime or ISO string into a Python datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise StoreError(f"invalid datetime value: {type(value).__name__}")


def _fresh_id() -> RecordID:
    """New surrogate id for a team_invite record."""
    return RecordID("team_invite", secrets.token_hex(16))


def _team_invite_from_row(row: dict[str, Any]) -> TeamInvite:
    """Build a `TeamInvite` from a database row."""
    return TeamInvite(
        id=row["id"],
        tenant_id=row["tenant_id"],
        token=row["token"],
        role=row["role"],
        created_by=row["created_by"],
        expires_at=_as_datetime(row["expires_at"]),
        status=row["status"],
        created_at=_as_datetime(row["created_at"]),
    )


def _now() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


def create_team_invite(
    db: Any,
    *,
    tenant_id: RecordID,
    created_by: RecordID,
    expires_at: datetime | None = None,
) -> TeamInvite:
    """Create a team invite for the given tenant.

    Who creates it must belong to the tenant; the route checks the owner role.
    """
    tenancy.assert_membership(db, user_id=created_by, tenant_id=tenant_id)

    if expires_at is None:
        expires_at = _now() + timedelta(days=7)

    rid = _fresh_id()
    token = secrets.token_hex(16)
    db.query(
        "CREATE $r SET tenant_id = $t_id, token = $tk, role = $r_role, "
        "created_by = $u_id, expires_at = $exp, status = 'pending', "
        "created_at = time::now();",
        {
            "r": rid,
            "t_id": tenant_id,
            "tk": token,
            "r_role": "member",
            "u_id": created_by,
            "exp": expires_at,
        },
    )
    invite = get_team_invite_by_token(db, token)
    if invite is None:
        raise StoreError("team_invite vanished during creation")
    return invite


def get_team_invite_by_token(db: Any, token: str) -> TeamInvite | None:
    """Look up a team invite by its unique token."""
    rows = db.query(
        "SELECT * FROM team_invite WHERE token = $tk LIMIT 1;",
        {"tk": token},
    )
    return _team_invite_from_row(rows[0]) if rows else None


def _find_membership(db: Any, *, user_id: RecordID, tenant_id: RecordID) -> Any:
    """Return the membership for (user, tenant), or None."""
    rows = db.query(
        "SELECT * FROM membership WHERE in = $u_id AND out = $t_id LIMIT 1;",
        {"u_id": user_id, "t_id": tenant_id},
    )
    return rows[0] if rows else None


def accept_team_invite(db: Any, *, token: str, user_id: RecordID) -> TeamInvite:
    """Accept a pending/non-expired invite and create a membership in the tenant.

    Rejects missing, expired, already-accepted or revoked tokens (`TeamInviteError`).
    If the user is already a member of the tenant, only marks the invite accepted.
    """
    invite = get_team_invite_by_token(db, token)
    if invite is None:
        raise TeamInviteError("team invite not found")
    if invite.status != "pending":
        raise TeamInviteError("team invite is not pending")
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        raise TeamInviteError("team invite has expired")

    existing = _find_membership(db, user_id=user_id, tenant_id=invite.tenant_id)
    if existing is None:
        tenancy.create_membership(
            db,
            user_id=user_id,
            tenant_id=invite.tenant_id,
            role=cast(Literal["owner", "member"], invite.role),
        )

    db.query(
        "UPDATE $r SET status = 'accepted' WHERE status = 'pending';",
        {"r": invite.id},
    )
    updated = get_team_invite_by_token(db, token)
    if updated is None or updated.status != "accepted":
        raise StoreError("team invite accept failed")
    return updated
