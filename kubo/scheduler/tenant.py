"""Resolução do tenant/user para jobs do scheduler (KUBO-123, KUBO-198)."""

from __future__ import annotations

import os
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import tenancy as tenancy_store

_log = structlog.get_logger(__name__)


def resolve_scheduler_tenant_and_user(db: Any) -> tuple[RecordID, RecordID]:
    """Devolve o tenant/user sob o qual o scheduler executa um job.

    Ordem de precedência:
    1. Env `KUBO_SCHEDULER_TENANT_ID` + `KUBO_SCHEDULER_USER_UID` (pinado).
    2. Primeiro tenant do banco + seu owner (fallback single-tenant).

    `KUBO_SCHEDULER_TENANT_ID` aceita `tenant:<key>` ou só `<key>`;
    `KUBO_SCHEDULER_USER_UID` é o firebase_uid, resolvido para record<user>.

    KUBO-198: o fallback (2) é instável (`ORDER BY id LIMIT 1` pega qualquer
    tenant). Quando usado, loga um warning explícito para que a operação saiba
    que o scheduler não está pinado — em produção, sempre setar as env vars."""
    tenant_raw = os.environ.get("KUBO_SCHEDULER_TENANT_ID", "").strip()
    uid = os.environ.get("KUBO_SCHEDULER_USER_UID", "").strip()
    if tenant_raw and uid:
        return _resolve_from_env(db, tenant_raw, uid)
    _log.warning(
        "scheduler_tenant_fallback",
        reason="KUBO_SCHEDULER_TENANT_ID/KUBO_SCHEDULER_USER_UID not set; "
        "using unstable get_first_tenant (ORDER BY id) — pin env vars in production",
    )
    return _resolve_first_tenant_owner(db)


def _resolve_from_env(db: Any, tenant_raw: str, uid: str) -> tuple[RecordID, RecordID]:
    user = tenancy_store.get_user_by_firebase_uid(db, uid)
    if user is None:
        raise ConfigError(f"KUBO_SCHEDULER_USER_UID '{uid}' does not resolve to a user")
    tenant = tenancy_store.parse_tenant_id(tenant_raw)
    if tenant is None:
        raise ConfigError(f"invalid KUBO_SCHEDULER_TENANT_ID: {tenant_raw}")
    return tenant, user.id


def _resolve_first_tenant_owner(db: Any) -> tuple[RecordID, RecordID]:
    tenant_id = tenancy_store.get_first_tenant(db)
    owner = tenancy_store.get_tenant_owner(db, tenant_id)
    return tenant_id, owner
