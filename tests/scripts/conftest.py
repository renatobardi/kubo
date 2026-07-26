"""Fixtures de integração para os testes de casca dos scripts one-off (sessão 0014).

`db` sobe um database SurrealDB próprio (schema do zero) E monkeypatcha
`client.config` para que o `main()` dos scripts — que chama
`client.connect(client.config())` internamente — conecte no MESMO database do
teste. Marcado `integration` (exige SurrealDB, como tests/store).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, migrations, tenancy

_SCRIPTS_DB = "test_scripts_casca"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Database efêmero + `client.config` apontado para ele (o script conecta no mesmo)."""
    cfg = replace(client.config(), database=_SCRIPTS_DB)
    monkeypatch.setattr(client, "config", lambda: cfg)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_SCRIPTS_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_SCRIPTS_DB};")


@pytest.fixture
def user_id(db: Any) -> RecordID:
    """Usuário de teste criado no banco."""
    return tenancy.create_user(db, firebase_uid="test-user-uid").id


@pytest.fixture
def tenant_id(db: Any, user_id: RecordID) -> RecordID:
    """Tenant de teste com o usuário como owner."""
    return tenancy.create_tenant(db, name="Test Tenant", owner_user_id=user_id).id
