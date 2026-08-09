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
from kubo.store import transaction
from kubo.store.scoped import ScopedStore


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
    session: ScopedStore,
    *,
    expires_at: datetime | None = None,
) -> TeamInvite:
    """Create a team invite for the session's tenant.

    The session's user is the creator; membership is checked at construction.
    """
    if expires_at is None:
        expires_at = _now() + timedelta(days=7)

    rid = _fresh_id()
    token = secrets.token_hex(16)
    session.query(
        "CREATE $r SET tenant_id = $tenant_id, token = $tk, role = $r_role, "
        "created_by = $user_id, expires_at = $exp, status = 'pending', "
        "created_at = time::now();",
        {
            "r": rid,
            "tk": token,
            "r_role": "member",
            "exp": expires_at,
        },
    )
    invite = get_team_invite_by_token(session, token)
    if invite is None:
        raise StoreError("team_invite vanished during creation")
    return invite


def get_team_invite_by_token(session: ScopedStore, token: str) -> TeamInvite | None:
    """Look up a team invite by its unique token within the session's tenant."""
    rows = session.query(
        "SELECT * FROM team_invite WHERE token = $tk AND tenant_id = $tenant_id LIMIT 1;",
        {"tk": token},
    )
    return _team_invite_from_row(rows[0]) if rows else None


def accept_team_invite(session: ScopedStore, *, token: str) -> TeamInvite:
    """Accept a pending/non-expired invite and create a membership in the tenant.

    Rejects missing, expired, already-accepted or revoked tokens (`TeamInviteError`).
    If the user is already a member of the tenant, only marks the invite accepted.
    The membership creation and invite status update run inside a single transaction.

    The session must be ``scoped_superadmin`` — the accepting user is not yet a
    member of the invite's tenant; the tenant_id comes from the invite, not from
    membership.
    """
    try:
        transaction.run_transaction(
            session,
            [
                "LET $invite = (SELECT * FROM team_invite WHERE token = $tk "
                "AND tenant_id = $tenant_id AND status = 'pending' "
                "AND expires_at > time::now() LIMIT 1)",
                "IF array::len($invite) == 0 { THROW 'TeamInviteError:invalid' }",
                "LET $tenant_id = $invite[0].tenant_id",
                "LET $role = $invite[0].role",
                "LET $existing = (SELECT * FROM membership WHERE in = $user_id "
                "AND out = $tenant_id LIMIT 1)",
                "IF array::len($existing) == 0 { RELATE $user_id->membership->$tenant_id "
                "CONTENT { role: $role, created_at: time::now() } }",
                "LET $updated = (UPDATE $invite[0].id SET status = 'accepted' "
                "WHERE status = 'pending' RETURN AFTER)",
                "IF array::len($updated) == 0 { THROW 'TeamInviteError:conflict' }",
            ],
            {"tk": token},
        )
    except StoreError as exc:
        if "TeamInviteError" in str(exc):
            raise TeamInviteError("team invite is invalid, expired, or already used") from exc
        raise

    updated = get_team_invite_by_token(session, token)
    if updated is None:
        raise TeamInviteError("team invite not found after accept")
    return updated
