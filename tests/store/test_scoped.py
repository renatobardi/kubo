"""ScopedStore: sessão escopada por tenant (ADR-0053, KUBO-209).

Integração (SurrealDB real): criação com checagem de membership, injeção de
`$tenant_id`/`$user_id` em queries, caminho transacional, factories distintas
e PoolReader para leituras do pool sem tenant.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import MembershipRequiredError
from kubo.store import client, migrations, tenancy, transaction
from kubo.store.scoped import PoolReader, ScopedStore, scoped, scoped_superadmin

pytestmark = pytest.mark.integration

_SCOPED_DB = "test_scoped"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_SCOPED_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_SCOPED_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_SCOPED_DB};")


@pytest.fixture
def user_tenant(db: Any) -> tuple[RecordID, RecordID]:
    """Cria user + tenant; devolve (user_id, tenant_id)."""
    user = tenancy.create_user(db, firebase_uid="scoped-test-uid")
    tenant = tenancy.create_tenant(db, name="Scoped Team", owner_user_id=user.id)
    return user.id, tenant.id


# ---------------------------------------------------------------------------
# Factory: scoped (acesso comum — checa membership)
# ---------------------------------------------------------------------------


def test_scoped_succeeds_with_valid_membership(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """Criar sessão com membership válido devolve ScopedStore."""
    user_id, tenant_id = user_tenant
    store = scoped(db, tenant_id=tenant_id, user_id=user_id)
    assert isinstance(store, ScopedStore)
    assert store.tenant_id == tenant_id
    assert store.user_id == user_id
    assert store.superadmin is False


def test_scoped_fails_without_membership(db: Any, user_tenant: tuple[RecordID, RecordID]) -> None:
    """Criar sessão sem membership levanta MembershipRequiredError."""
    _user_id, tenant_id = user_tenant
    outsider = tenancy.create_user(db, firebase_uid="outsider-uid")
    with pytest.raises(MembershipRequiredError):
        scoped(db, tenant_id=tenant_id, user_id=outsider.id)


def test_direct_construction_checks_membership(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """Construir ScopedStore direto (sem factory) também checa membership.

    A checagem mora no construtor: construção direta com `superadmin=False`
    (o default) não é porta de bypass (CodeRabbit PR #237).
    """
    user_id, tenant_id = user_tenant
    outsider = tenancy.create_user(db, firebase_uid="direct-outsider-uid")
    with pytest.raises(MembershipRequiredError):
        ScopedStore(db, tenant_id=tenant_id, user_id=outsider.id)

    store = ScopedStore(db, tenant_id=tenant_id, user_id=user_id)
    assert store.superadmin is False


def test_direct_construction_superadmin_skips_membership(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """`superadmin=True` literal dispensa membership — sítio greppável (ADR-0053 §4)."""
    _user_id, tenant_id = user_tenant
    admin = tenancy.create_user(db, firebase_uid="direct-admin-uid")
    store = ScopedStore(db, tenant_id=tenant_id, user_id=admin.id, superadmin=True)
    assert store.superadmin is True


# ---------------------------------------------------------------------------
# Factory: scoped_superadmin (acesso administrativo — dispensa membership)
# ---------------------------------------------------------------------------


def test_scoped_superadmin_skips_membership(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """Superadmin não precisa de membership; tenant_id continua obrigatório."""
    _user_id, tenant_id = user_tenant
    admin = tenancy.create_user(db, firebase_uid="admin-uid")
    store = scoped_superadmin(db, user_id=admin.id, tenant_id=tenant_id)
    assert isinstance(store, ScopedStore)
    assert store.superadmin is True
    assert store.tenant_id == tenant_id


def test_scoped_superadmin_still_injects_tenant(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """Superadmin dispensa membership, nunca o filtro — $tenant_id é injetado."""
    _user_id, tenant_id = user_tenant
    admin = tenancy.create_user(db, firebase_uid="admin-uid")
    store = scoped_superadmin(db, user_id=admin.id, tenant_id=tenant_id)
    rows = store.query("SELECT * FROM tenant WHERE id = $tenant_id;")
    assert len(rows) == 1
    assert rows[0]["id"] == tenant_id


# ---------------------------------------------------------------------------
# Injeção de params
# ---------------------------------------------------------------------------


def test_query_injects_tenant_id_and_user_id(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """Query emitida pela sessão chega ao banco com $tenant_id e $user_id nos params."""
    user_id, tenant_id = user_tenant
    store = scoped(db, tenant_id=tenant_id, user_id=user_id)
    rows = store.query("SELECT * FROM tenant WHERE id = $tenant_id;")
    assert len(rows) == 1
    assert rows[0]["id"] == tenant_id

    rows2 = store.query("SELECT * FROM user WHERE id = $user_id;")
    assert len(rows2) == 1
    assert rows2[0]["id"] == user_id


def test_query_does_not_require_caller_to_pass_tenant(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """Caller não passa tenant_id/user_id — a sessão injeta."""
    user_id, tenant_id = user_tenant
    store = scoped(db, tenant_id=tenant_id, user_id=user_id)
    # Query com params próprios do domínio, sem tenant_id/user_id
    rows = store.query(
        "SELECT * FROM tenant WHERE id = $tenant_id AND name = $name;", {"name": "Scoped Team"}
    )
    assert len(rows) == 1


def test_query_raw_injects_tenant_id(db: Any, user_tenant: tuple[RecordID, RecordID]) -> None:
    """query_raw também injeta $tenant_id — caminho de escrita não escapa."""
    user_id, tenant_id = user_tenant
    store = scoped(db, tenant_id=tenant_id, user_id=user_id)
    raw = store.query_raw("SELECT * FROM tenant WHERE id = $tenant_id;")
    assert raw["result"][0]["status"] == "OK"
    assert len(raw["result"][0]["result"]) == 1


# ---------------------------------------------------------------------------
# Caminho transacional
# ---------------------------------------------------------------------------


def test_run_transaction_inherits_injection(
    db: Any, user_tenant: tuple[RecordID, RecordID]
) -> None:
    """run_transaction via ScopedStore herda $tenant_id — escrita transacional não escapa."""
    user_id, tenant_id = user_tenant
    store = scoped(db, tenant_id=tenant_id, user_id=user_id)

    # Cria um destination para o tenant via transação
    dest_id = RecordID("destination", "tx-test-dest")
    transaction.run_transaction(
        store,
        [
            "CREATE $d SET name = 'Test', kind = 'pessoa', channel = 'telegram', "
            "address = '123', enabled = true, archived_at = NONE, "
            "tenant_id = $tenant_id, created_at = time::now()",
        ],
        {"d": dest_id},
    )

    # Lê de volta filtrando por tenant
    rows = store.query(
        "SELECT * FROM destination WHERE id = $d AND tenant_id = $tenant_id;", {"d": dest_id}
    )
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == tenant_id


# ---------------------------------------------------------------------------
# Injeção: prova observável via spy (sem banco)
# ---------------------------------------------------------------------------


class _SpyDb:
    """Conexão falsa que registra (sql, params) de cada chamada."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        self.calls.append((sql, params))
        return []

    def query_raw(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((sql, params))
        return {"result": []}

    def use(self, namespace: str, database: str) -> None:
        pass


def test_scoped_store_injection_overrides_caller_params() -> None:
    """tenant_id/user_id do caller NUNCA vencem os da sessão (segurança)."""
    spy = _SpyDb()
    store = ScopedStore(
        spy,
        tenant_id=RecordID("tenant", "real"),
        user_id=RecordID("user", "real"),
        superadmin=True,  # dispensa membership — sem banco neste teste
    )
    store.query("SELECT * FROM flow WHERE tenant_id = $tenant_id;", {"tenant_id": "evil"})
    assert spy.calls[0][1] == {
        "tenant_id": RecordID("tenant", "real"),
        "user_id": RecordID("user", "real"),
    }


# ---------------------------------------------------------------------------
# PoolReader
# ---------------------------------------------------------------------------


def test_pool_reader_query_does_not_inject_tenant() -> None:
    """PoolReader passa os params intactos — nenhuma injeção de tenant/user."""
    spy = _SpyDb()
    reader = PoolReader(spy)
    reader.query("SELECT * FROM item LIMIT 1;", {"k": "v"})
    assert spy.calls == [("SELECT * FROM item LIMIT 1;", {"k": "v"})]


# ---------------------------------------------------------------------------
# Não existe sessão sem tenant
# ---------------------------------------------------------------------------


def test_scoped_store_cannot_exist_without_tenant() -> None:
    """ScopedStore sem tenant_id não é representável — construtor exige ambos."""
    # O tipo em si carrega a garantia: tenant_id é obrigatório no __init__.
    # Este teste documenta que não há factory que produza ScopedStore sem tenant.
    with pytest.raises(TypeError):
        ScopedStore(None, user_id=RecordID("user", "x"))  # type: ignore[call-arg]
