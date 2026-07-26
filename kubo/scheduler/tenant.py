"""Resolução do tenant/user para jobs do scheduler (KUBO-123)."""

from __future__ import annotations

import os
from typing import Any

from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import tenancy as tenancy_store


def resolve_scheduler_tenant_and_user(db: Any) -> tuple[RecordID, RecordID]:
    """Devolve o tenant/user sob o qual o scheduler executa um job.

    Ordem de precedência:
    1. Env `KUBO_SCHEDULER_TENANT_ID` + `KUBO_SCHEDULER_USER_UID`.
    2. Primeiro tenant do banco + seu owner (fallback single-tenant).

    `KUBO_SCHEDULER_TENANT_ID` aceita `tenant:<key>` ou só `<key>`;
    `KUBO_SCHEDULER_USER_UID` é o firebase_uid, resolvido para record<user>."""
    tenant_raw = os.environ.get("KUBO_SCHEDULER_TENANT_ID", "").strip()
    uid = os.environ.get("KUBO_SCHEDULER_USER_UID", "").strip()
    if tenant_raw and uid:
        return _resolve_from_env(db, tenant_raw, uid)
    return _resolve_first_tenant_owner(db)


def _resolve_from_env(db: Any, tenant_raw: str, uid: str) -> tuple[RecordID, RecordID]:
    user = tenancy_store.get_user_by_firebase_uid(db, uid)
    if user is None:
        raise ConfigError(f"KUBO_SCHEDULER_USER_UID '{uid}' não resolve um user")
    tenant = _parse_tenant(tenant_raw)
    return tenant, user.id


def _parse_tenant(raw: str) -> RecordID:
    key = raw.strip()
    if ":" in key:
        table, _, key = key.partition(":")
        if table != "tenant" or not key:
            raise ConfigError(f"KUBO_SCHEDULER_TENANT_ID inválido: {raw}")
    return RecordID("tenant", key)


def _resolve_first_tenant_owner(db: Any) -> tuple[RecordID, RecordID]:
    tenants = db.query("SELECT id FROM tenant LIMIT 1;")
    if not tenants:
        raise ConfigError("nenhum tenant encontrado para o scheduler")
    tenant_id = tenants[0]["id"]
    rows = db.query(
        "SELECT in AS user FROM membership WHERE out = $t AND role = 'owner' LIMIT 1;",
        {"t": tenant_id},
    )
    if not rows:
        raise ConfigError(f"tenant {tenant_id} não tem owner")
    return tenant_id, rows[0]["user"]
