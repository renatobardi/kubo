"""Team invite (team_invite) — onboarding members into a tenant (ADR-0041, KUBO-120).

Distinct from the Telegram onboarding invite (`kubo/store/invites.py`): high-entropy
token, scoped to one tenant, with status `pending`/`accepted`/`expired`/`revoked`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from surrealdb import RecordID

from kubo.errors import StoreError, TeamInviteError
from kubo.store import tenancy, transaction


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


def accept_team_invite(db: Any, *, token: str, user_id: RecordID) -> TeamInvite:
    """Accept a pending/non-expired invite and create a membership in the tenant.

    Rejects missing, expired, already-accepted or revoked tokens (`TeamInviteError`).
    If the user is already a member of the tenant, only marks the invite accepted.
    The membership creation and invite status update run inside a single transaction.
    """
    try:
        transaction.run_transaction(
            db,
            [
                "LET $invite = (SELECT * FROM team_invite WHERE token = $tk "
                "AND status = 'pending' AND expires_at > time::now() LIMIT 1)",
                "IF array::len($invite) == 0 { THROW 'TeamInviteError:invalid' }",
                "LET $tenant_id = $invite[0].tenant_id",
                "LET $role = $invite[0].role",
                "LET $existing = (SELECT * FROM membership WHERE in = $u_id "
                "AND out = $tenant_id LIMIT 1)",
                "IF array::len($existing) == 0 { RELATE $u_id->membership->$tenant_id "
                "CONTENT { role: $role, created_at: time::now() } }",
                "LET $updated = (UPDATE $invite[0].id SET status = 'accepted' "
                "WHERE status = 'pending' RETURN AFTER)",
                "IF array::len($updated) == 0 { THROW 'TeamInviteError:conflict' }",
            ],
            {"tk": token, "u_id": user_id},
        )
    except StoreError as exc:
        if "TeamInviteError" in str(exc):
            raise TeamInviteError("team invite is invalid, expired, or already used") from exc
        raise

    updated = get_team_invite_by_token(db, token)
    if updated is None:
        raise TeamInviteError("team invite not found after accept")
    return updated
