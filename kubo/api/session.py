"""Resolução de sessão para tenant/user ativo (KUBO-123)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from surrealdb import RecordID

from kubo.store import tenancy as tenancy_store


def resolve_session(db: Any, request: Request) -> tuple[RecordID, RecordID] | None:
    """Devolve o par (tenant_id, user_id) da sessão autenticada, ou None se inválido.

    O `tenant_id` na sessão pode estar na forma `tenant:<key>` (canonical) ou só `<key>`;
    o `uid` é o firebase_uid, resolvido para o record<user>."""
    uid = request.session.get("uid")
    tenant_id = request.session.get("tenant_id")
    if not uid or not tenant_id:
        return None
    user = tenancy_store.get_user_by_firebase_uid(db, uid)
    if user is None:
        return None
    tenant = _parse_tenant(tenant_id)
    if tenant is None:
        return None
    if not _is_member(db, user_id=user.id, tenant_id=tenant):
        return None
    return tenant, user.id


def _is_member(db: Any, *, user_id: RecordID, tenant_id: RecordID) -> bool:
    """True se o usuário tem membership no tenant."""
    rows = db.query(
        "SELECT id FROM membership WHERE in = $u AND out = $t LIMIT 1;",
        {"u": user_id, "t": tenant_id},
    )
    return bool(rows)


def _parse_tenant(raw: str) -> RecordID | None:
    """`tenant:<key>` ou `<key>` → RecordID da tabela `tenant`."""
    key = raw.strip()
    if not key:
        return None
    if ":" in key:
        table, _, key = key.partition(":")
        if table != "tenant" or not key:
            return None
    return RecordID("tenant", key)
