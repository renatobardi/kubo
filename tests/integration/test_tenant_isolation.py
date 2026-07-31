"""Teste E2E de isolamento cross-tenant em rotas HTTP (KUBO-130).

Integração (SurrealDB real): duas sessões em tenants distintos provam que a
rota /entities só devolve entidades do tenant ativo, sem mock de membership.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

os.environ.setdefault("KUBO_TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")

from kubo.api.app import create_app  # noqa: E402
from kubo.api.auth import hash_password  # noqa: E402
from kubo.store import client as store_client  # noqa: E402
from kubo.store import migrations, tenancy

pytestmark = pytest.mark.integration

UI_PASSWORD = "test-ui-password"  # pragma: allowlist secret
_BREAKGLASS_USER_ID = RecordID("user", "breakglass-owner")
_BREAKGLASS_TENANT_ID = RecordID("tenant", "breakglass")
_BREAKGLASS_UID = "user:breakglass-owner"
_BREAKGLASS_USER_ID_STR = "user:breakglass-owner"
_BREAKGLASS_TENANT_ID_STR = "tenant:breakglass"
_SUPERADMIN_UID = "uid-superadmin-123"
# O login Firebase GRAVA (user/tenant/membership) e por isso abre `connect_rw` (ADR-0018);
# sem o kubo_rw EDITOR a rota responderia 503 em vez de 303.
_RW_PASS = secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def _ui_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env mínimo para a fábrica subir; senha conhecida para login."""
    monkeypatch.setenv("KUBO_PASSWORD_HASH", hash_password(UI_PASSWORD))
    monkeypatch.setenv(
        "SESSION_SECRET",
        "test-session-secret-0123456789abcdef",  # pragma: allowlist secret
    )
    monkeypatch.setenv("KUBO_FIREBASE_PROJECT_ID", "kubo-test-project")
    monkeypatch.setenv("KUBO_FIREBASE_OWNER_UIDS", _SUPERADMIN_UID)
    monkeypatch.setenv("KUBO_FIREBASE_API_KEY", "test-firebase-api-key")  # pragma: allowlist secret
    monkeypatch.setenv("KUBO_BREAKGLASS_USER_ID", _BREAKGLASS_USER_ID_STR)
    monkeypatch.setenv("KUBO_BREAKGLASS_TENANT_ID", _BREAKGLASS_TENANT_ID_STR)
    monkeypatch.setenv(
        "KUBO_TELEGRAM_WEBHOOK_SECRET",
        "test-webhook-secret",  # pragma: allowlist secret
    )
    monkeypatch.setenv("SURREAL_DB", "test_tenant_isolation")
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.delenv("KUBO_ALLOWED_HOSTS", raising=False)


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(store_client.config(), database="test_tenant_isolation")
    with store_client.connect(cfg) as conn:
        conn.query("REMOVE DATABASE IF EXISTS test_tenant_isolation;")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        conn.query(f"DEFINE USER OVERWRITE kubo_rw ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")

        # Breakglass user/tenant para login via /login
        conn.query(
            "CREATE $u SET firebase_uid = $uid, email = None, created_at = time::now();",
            {"u": _BREAKGLASS_USER_ID, "uid": _BREAKGLASS_UID},
        )
        conn.query(
            "CREATE $t SET name = 'Breakglass', created_at = time::now();",
            {"t": _BREAKGLASS_TENANT_ID},
        )
        conn.query(
            "RELATE $u->membership->$t SET role = 'owner', created_at = time::now();",
            {"u": _BREAKGLASS_USER_ID, "t": _BREAKGLASS_TENANT_ID},
        )

        yield conn
        conn.query("REMOVE USER IF EXISTS kubo_rw ON ROOT;")
        conn.query("REMOVE DATABASE IF EXISTS test_tenant_isolation;")


@pytest.fixture
def test_client() -> TestClient:
    """Client anônimo (base_url=https para cookie Secure)."""
    return TestClient(create_app(), base_url="https://testserver")


def _login(test_client: TestClient) -> None:
    """Faz login break-glass e guarda o cookie de sessão."""
    resp = test_client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303


def _switch(test_client: TestClient, tenant_id: RecordID) -> None:
    """Muda o tenant ativo da sessão."""
    resp = test_client.post(
        "/auth/switch",
        data={"tenant_id": str(tenant_id), "next": "/entities"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 200)


def test_entities_route_is_tenant_scoped(db: Any, test_client: TestClient) -> None:
    """Sessão no tenant A só enxerga entidade de A; após switch para B, só a de B."""
    _login(test_client)

    # Cria dois tenants adicionais para o mesmo usuário breakglass
    tenant_a = tenancy.create_tenant(db, name="Tenant A", owner_user_id=_BREAKGLASS_USER_ID)
    tenant_b = tenancy.create_tenant(db, name="Tenant B", owner_user_id=_BREAKGLASS_USER_ID)

    # Uma entidade em cada tenant
    from kubo.store.knowledge import get_or_create_entity

    get_or_create_entity(db, tenant_id=tenant_a.id, user_id=_BREAKGLASS_USER_ID, name="Entity A")
    get_or_create_entity(db, tenant_id=tenant_b.id, user_id=_BREAKGLASS_USER_ID, name="Entity B")

    _switch(test_client, tenant_a.id)
    html_a = test_client.get("/entities").text
    assert "Entity A" in html_a
    assert "Entity B" not in html_a

    _switch(test_client, tenant_b.id)
    html_b = test_client.get("/entities").text
    assert "Entity B" in html_b
    assert "Entity A" not in html_b


def test_superadmin_can_browse_foreign_tenant(
    db: Any, test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Superadmin autenticado via Firebase cria user, troca para tenant alheio e lê entidades."""
    normal_user = tenancy.create_user(db, firebase_uid="normal-user", email="normal@example.com")
    normal_tenant = tenancy.create_tenant(db, name="Normal Tenant", owner_user_id=normal_user.id)

    from kubo.store.knowledge import get_or_create_entity

    get_or_create_entity(
        db,
        tenant_id=normal_tenant.id,
        user_id=normal_user.id,
        name="Foreign Entity",
    )

    from kubo.api.routes import auth as auth_mod

    def _fake_verify(*_args: Any, **_kw: Any) -> dict[str, Any]:
        return {"uid": _SUPERADMIN_UID, "email": "admin@example.com", "provider": "firebase"}

    monkeypatch.setattr(auth_mod, "verify_id_token", _fake_verify)

    resp = test_client.post(
        "/auth/firebase",
        json={"id_token": "fake-token"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    _switch(test_client, normal_tenant.id)
    html = test_client.get("/entities").text
    assert "Foreign Entity" in html


def test_cross_tenant_entity_detail_returns_403(db: Any, test_client: TestClient) -> None:
    """Sessão no tenant A recebe 403 ao acessar o detalhe de entidade do tenant B
    via HTTP (KUBO-130, letra da spec: 403/404 em acesso cross-tenant direto)."""
    _login(test_client)

    tenant_a = tenancy.create_tenant(db, name="Tenant A2", owner_user_id=_BREAKGLASS_USER_ID)
    tenant_b = tenancy.create_tenant(db, name="Tenant B2", owner_user_id=_BREAKGLASS_USER_ID)

    from kubo.store.knowledge import get_or_create_entity

    entity_b = get_or_create_entity(
        db, tenant_id=tenant_b.id, user_id=_BREAKGLASS_USER_ID, name="Entity B2"
    )

    _switch(test_client, tenant_a.id)
    entity_key = str(entity_b).partition(":")[2]
    resp = test_client.get(f"/entities/{entity_key}", follow_redirects=False)
    assert resp.status_code == 403


def test_non_superadmin_cannot_switch_to_foreign_tenant(db: Any, test_client: TestClient) -> None:
    """Non-superadmin que loga via /login não consegue trocar para um tenant ao qual
    não pertence — /auth/switch retorna 403 (CodeRabbit review, KUBO-126)."""
    _login(test_client)

    foreign_user = tenancy.create_user(db, firebase_uid="foreign-user", email="foreign@example.com")
    foreign_tenant = tenancy.create_tenant(db, name="Foreign Tenant", owner_user_id=foreign_user.id)

    resp = test_client.post(
        "/auth/switch",
        data={"tenant_id": str(foreign_tenant.id), "next": "/entities"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# ── KUBO-128: isolamento de tenant em dispatch, source (legado) e run ──────────


def test_sources_route_is_tenant_scoped(db: Any, test_client: TestClient) -> None:
    """Sessão no tenant A só enxerga source de A; após switch para B, só a de B (KUBO-128)."""
    _login(test_client)

    tenant_a = tenancy.create_tenant(db, name="Src Tenant A", owner_user_id=_BREAKGLASS_USER_ID)
    tenant_b = tenancy.create_tenant(db, name="Src Tenant B", owner_user_id=_BREAKGLASS_USER_ID)

    from kubo.store.knowledge import create_source

    create_source(
        db,
        tenant_id=tenant_a.id,
        user_id=_BREAKGLASS_USER_ID,
        kind="rss",
        canonical="https://a.example.com/feed.xml",
        title="Feed A",
    )
    create_source(
        db,
        tenant_id=tenant_b.id,
        user_id=_BREAKGLASS_USER_ID,
        kind="rss",
        canonical="https://b.example.com/feed.xml",
        title="Feed B",
    )

    _switch(test_client, tenant_a.id)
    html_a = test_client.get("/sources").text
    assert "Feed A" in html_a
    assert "Feed B" not in html_a

    _switch(test_client, tenant_b.id)
    html_b = test_client.get("/sources").text
    assert "Feed B" in html_b
    assert "Feed A" not in html_b


def test_runs_route_is_tenant_scoped(db: Any, test_client: TestClient) -> None:
    """Sessão no tenant A só enxerga run de A; após switch para B, só a de B (KUBO-128)."""
    _login(test_client)

    tenant_a = tenancy.create_tenant(db, name="Run Tenant A", owner_user_id=_BREAKGLASS_USER_ID)
    tenant_b = tenancy.create_tenant(db, name="Run Tenant B", owner_user_id=_BREAKGLASS_USER_ID)

    from kubo.store.knowledge import start_run

    start_run(db, tenant_id=tenant_a.id, user_id=_BREAKGLASS_USER_ID, worker="feed-a")
    start_run(db, tenant_id=tenant_b.id, user_id=_BREAKGLASS_USER_ID, worker="feed-b")

    _switch(test_client, tenant_a.id)
    html_a = test_client.get("/runs").text
    assert "1 de 1" in html_a
    assert "feed-a" in html_a
    assert "feed-b" not in html_a

    _switch(test_client, tenant_b.id)
    html_b = test_client.get("/runs").text
    assert "1 de 1" in html_b
    assert "feed-b" in html_b
    assert "feed-a" not in html_b


def test_dispatches_route_is_tenant_scoped(db: Any, test_client: TestClient) -> None:
    """Sessão no tenant A só enxerga dispatch de A; após switch para B, só a de B (KUBO-128)."""
    _login(test_client)

    tenant_a = tenancy.create_tenant(db, name="Disp Tenant A", owner_user_id=_BREAKGLASS_USER_ID)
    tenant_b = tenancy.create_tenant(db, name="Disp Tenant B", owner_user_id=_BREAKGLASS_USER_ID)

    from kubo.store.destinations import create_destination
    from kubo.store.knowledge import insert_dispatch

    dest_a = create_destination(
        db,
        name="Destino A",
        kind="pessoa",
        channel="telegram",
        address="111",
        tenant_id=tenant_a.id,
    )
    dest_b = create_destination(
        db,
        name="Destino B",
        kind="pessoa",
        channel="telegram",
        address="222",
        tenant_id=tenant_b.id,
    )
    insert_dispatch(
        db,
        tenant_id=tenant_a.id,
        user_id=_BREAKGLASS_USER_ID,
        destination=dest_a,
        channel="telegram",
        status="ok",
        watermark=None,
        item_count=1,
        items=[],
    )
    insert_dispatch(
        db,
        tenant_id=tenant_b.id,
        user_id=_BREAKGLASS_USER_ID,
        destination=dest_b,
        channel="telegram",
        status="ok",
        watermark=None,
        item_count=2,
        items=[],
    )

    _switch(test_client, tenant_a.id)
    html_a = test_client.get("/dispatches").text
    assert "1 de 1" in html_a
    assert dest_a.id in html_a
    assert dest_b.id not in html_a

    _switch(test_client, tenant_b.id)
    html_b = test_client.get("/dispatches").text
    assert "1 de 1" in html_b
    assert dest_b.id in html_b
    assert dest_a.id not in html_b
