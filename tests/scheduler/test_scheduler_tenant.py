"""Testes unitários da resolução de tenant/user do scheduler (KUBO-198).

Cobre `resolve_scheduler_tenant_and_user` nos dois caminhos:
1. Env vars `KUBO_SCHEDULER_TENANT_ID` + `KUBO_SCHEDULER_USER_UID` (pinado).
2. Fallback `get_first_tenant` (sem env vars) — com warning de hardening.

O fallback é instável por construção (`ORDER BY id LIMIT 1` pega qualquer
tenant). O hardening (KUBO-198) loga um warning explícito quando o fallback
é usado, para que a operação saiba que o scheduler não está pinado.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.scheduler.tenant import resolve_scheduler_tenant_and_user

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_UID = "user:breakglass-owner"


class _FakeUser:
    def __init__(self, uid: str) -> None:
        self.id = RecordID("user", "breakglass-owner")
        self.firebase_uid = uid


def _make_db() -> Any:
    """Mock de db — o mocking real acontece no fixture _patch_tenancy."""
    return MagicMock()


@pytest.fixture
def _patch_tenancy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patches tenancy_store functions used by resolve_scheduler_tenant_and_user."""
    user = _FakeUser(_UID)

    def fake_get_user_by_firebase_uid(db: Any, uid: str) -> Any:
        if uid == _UID:
            return user
        return None

    def fake_parse_tenant_id(raw: str) -> RecordID | None:
        key = raw.strip()
        if not key:
            return None
        if ":" in key:
            table, _, k = key.partition(":")
            if table != "tenant" or not k:
                return None
            return RecordID("tenant", k)
        return RecordID("tenant", key)

    def fake_get_first_tenant(db: Any) -> RecordID:
        return _TENANT

    def fake_get_tenant_owner(db: Any, tenant_id: RecordID) -> RecordID:
        return _USER

    monkeypatch.setattr(
        "kubo.scheduler.tenant.tenancy_store.get_user_by_firebase_uid",
        fake_get_user_by_firebase_uid,
    )
    monkeypatch.setattr(
        "kubo.scheduler.tenant.tenancy_store.parse_tenant_id",
        fake_parse_tenant_id,
    )
    monkeypatch.setattr(
        "kubo.scheduler.tenant.tenancy_store.get_first_tenant",
        fake_get_first_tenant,
    )
    monkeypatch.setattr(
        "kubo.scheduler.tenant.tenancy_store.get_tenant_owner",
        fake_get_tenant_owner,
    )


# --- Env var path (pinado) ---------------------------------------------------


def test_env_vars_resolve_to_tenant_and_user(
    _patch_tenancy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env vars setadas → resolve pelo env, não pelo fallback."""
    monkeypatch.setenv("KUBO_SCHEDULER_TENANT_ID", "tenant:breakglass")
    monkeypatch.setenv("KUBO_SCHEDULER_USER_UID", _UID)
    db = _make_db()
    tenant, user = resolve_scheduler_tenant_and_user(db)
    assert tenant == _TENANT
    assert user == _USER


def test_env_vars_accept_bare_key(_patch_tenancy: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """KUBO_SCHEDULER_TENANT_ID aceita 'breakglass' (sem prefixo tenant:)."""
    monkeypatch.setenv("KUBO_SCHEDULER_TENANT_ID", "breakglass")
    monkeypatch.setenv("KUBO_SCHEDULER_USER_UID", _UID)
    db = _make_db()
    tenant, user = resolve_scheduler_tenant_and_user(db)
    assert tenant == _TENANT
    assert user == _USER


def test_invalid_tenant_id_raises_config_error(
    _patch_tenancy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO_SCHEDULER_TENANT_ID inválido (table errada) → ConfigError."""
    monkeypatch.setenv("KUBO_SCHEDULER_TENANT_ID", "foo:breakglass")
    monkeypatch.setenv("KUBO_SCHEDULER_USER_UID", _UID)
    db = _make_db()
    with pytest.raises(ConfigError, match="invalid KUBO_SCHEDULER_TENANT_ID"):
        resolve_scheduler_tenant_and_user(db)


def test_unknown_uid_raises_config_error(
    _patch_tenancy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO_SCHEDULER_USER_UID que não resolve a user → ConfigError."""
    monkeypatch.setenv("KUBO_SCHEDULER_TENANT_ID", "tenant:breakglass")
    monkeypatch.setenv("KUBO_SCHEDULER_USER_UID", "nonexistent:uid")
    db = _make_db()
    with pytest.raises(ConfigError, match="does not resolve to a user"):
        resolve_scheduler_tenant_and_user(db)


# --- Fallback path (sem env vars) + hardening --------------------------------


def test_fallback_resolves_first_tenant(
    _patch_tenancy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem env vars → fallback para get_first_tenant."""
    monkeypatch.delenv("KUBO_SCHEDULER_TENANT_ID", raising=False)
    monkeypatch.delenv("KUBO_SCHEDULER_USER_UID", raising=False)
    db = _make_db()
    tenant, user = resolve_scheduler_tenant_and_user(db)
    assert tenant == _TENANT
    assert user == _USER


def test_fallback_logs_warning(_patch_tenancy: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hardening (KUBO-198): fallback sem env vars loga warning explícito."""
    from structlog.testing import capture_logs

    monkeypatch.delenv("KUBO_SCHEDULER_TENANT_ID", raising=False)
    monkeypatch.delenv("KUBO_SCHEDULER_USER_UID", raising=False)
    db = _make_db()
    with capture_logs() as logs:
        resolve_scheduler_tenant_and_user(db)
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert len(warnings) >= 1
    assert warnings[0]["event"] == "scheduler_tenant_fallback"
    assert "KUBO_SCHEDULER" in warnings[0]["reason"]


def test_pinned_does_not_log_warning(_patch_tenancy: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scheduler pinado (env vars setadas) NÃO loga warning de fallback."""
    from structlog.testing import capture_logs

    monkeypatch.setenv("KUBO_SCHEDULER_TENANT_ID", "tenant:breakglass")
    monkeypatch.setenv("KUBO_SCHEDULER_USER_UID", _UID)
    db = _make_db()
    with capture_logs() as logs:
        resolve_scheduler_tenant_and_user(db)
    fallback_warnings = [
        e for e in logs if e["log_level"] == "warning" and e["event"] == "scheduler_tenant_fallback"
    ]
    assert len(fallback_warnings) == 0


def test_partial_env_vars_fall_back(_patch_tenancy: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Só tenant setado (uid vazio) → fallback, não erro."""
    monkeypatch.setenv("KUBO_SCHEDULER_TENANT_ID", "tenant:breakglass")
    monkeypatch.delenv("KUBO_SCHEDULER_USER_UID", raising=False)
    db = _make_db()
    tenant, user = resolve_scheduler_tenant_and_user(db)
    assert tenant == _TENANT
    assert user == _USER
