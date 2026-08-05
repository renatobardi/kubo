"""Fixtures compartilhadas para testes de store que precisam de um tenant efêmero."""

from __future__ import annotations

from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import tenancy


@pytest.fixture
def user_id(db: Any) -> RecordID:
    """Usuário de teste criado no banco."""
    return tenancy.create_user(db, firebase_uid="test-user-uid").id


@pytest.fixture
def tenant_id(db: Any, user_id: RecordID) -> RecordID:
    """Tenant de teste com o usuário como owner."""
    return tenancy.create_tenant(db, name="Test Tenant", owner_user_id=user_id).id


@pytest.fixture
def other_user_id(db: Any, tenant_id: RecordID) -> RecordID:
    """Outro usuário de teste, membro do tenant (para isolamento user-scoped)."""
    user_id = tenancy.create_user(db, firebase_uid="test-other-uid").id
    tenancy.create_membership(db, user_id=user_id, tenant_id=tenant_id, role="member")
    return user_id
