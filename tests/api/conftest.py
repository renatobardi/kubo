"""Fixtures da suíte da UI: env de auth (autouse) + clients (anônimo e autenticado).

A fábrica passou a exigir `KUBO_PASSWORD_HASH` + `SESSION_SECRET` (fail-fast do
ADR-0014); `ui_env` é autouse para que qualquer `create_app()` na suíte tenha o
env mínimo. `KUBO_ALLOWED_HOSTS` fica sem setar de propósito: o default inclui
`testserver`, o Host que o TestClient envia."""

from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

# Valores defaults para a fábrica subir durante a importação do módulo.
# `ui_env` sobrescreve em runtime; o webhook cacheia o secret no import.
os.environ.setdefault("KUBO_TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")

from kubo.api.app import create_app
from kubo.api.auth import hash_password
from kubo.store.destinations import Destination
from kubo.store.knowledge import DashboardCounts
from kubo.store.settings import Settings

UI_PASSWORD = "test-ui-password"  # pragma: allowlist secret


@contextmanager
def _fake_connect(cfg: Any = None) -> Any:
    """Conexão falsa: as leituras da store estão stubadas, o db é ignorado."""
    yield object()


def _mobile_header_block(html: str) -> str:
    """Isola o `<header>` mobile (large-title) do resto do HTML renderizado — usado
    pelos testes de mobile_back_href/mobile_title (sessão 0019)."""
    start = html.find("text-[1.875rem]")
    header_start = html.rfind("<header", 0, start)
    return html[header_start : html.find("</header>", start) + len("</header>")]


@pytest.fixture(autouse=True)
def stub_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desacopla as rotas de página do banco real: connect falso + leituras vazias
    por padrão. Testes com dados específicos (test_distilled, test_dashboard)
    sobrescrevem estas leituras no corpo do teste."""
    for mod in (
        "auth",
        "dashboard",
        "distilled",
        "flows",
        "runs",
        "sources",
        "entities",
        "dispatches",
        "destinations",
    ):
        monkeypatch.setattr(f"kubo.api.routes.{mod}.client.connect", _fake_connect)
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.knowledge.dashboard_counts",
        lambda db, **kw: DashboardCounts(distilled=0, items=0, sources=0, entities=0),
    )
    monkeypatch.setattr("kubo.api.routes.dashboard.knowledge.recent_runs", lambda db, **kw: [])
    _breakglass_user = SimpleNamespace(
        id=RecordID("user", "breakglass-owner"),
        firebase_uid="user:breakglass-owner",
        email=None,
    )
    _breakglass_tenant = SimpleNamespace(
        id=RecordID("tenant", "breakglass"),
        name="Breakglass",
        created_at=None,
    )
    _breakglass_membership = SimpleNamespace(
        tenant=RecordID("tenant", "breakglass"),
        role="owner",
    )
    monkeypatch.setattr(
        "kubo.store.tenancy.get_user_by_firebase_uid", lambda db, firebase_uid: _breakglass_user
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.create_user",
        lambda db, *, firebase_uid, email=None: _breakglass_user,
    )
    monkeypatch.setattr(
        "kubo.store.tenancy.list_memberships_for_user", lambda db, user_id: [_breakglass_membership]
    )
    monkeypatch.setattr("kubo.store.tenancy.get_tenant", lambda db, tenant_id: _breakglass_tenant)
    monkeypatch.setattr(
        "kubo.store.tenancy.list_tenants",
        lambda db, tenant_ids: [_breakglass_tenant] if tenant_ids else [],
    )
    monkeypatch.setattr("kubo.store.tenancy.is_member", lambda db, *, user_id, tenant_id: True)
    monkeypatch.setattr("kubo.api.routes.distilled.knowledge.list_distilled", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.distilled.knowledge.count_distilled", lambda db, **kw: 0)
    monkeypatch.setattr("kubo.api.routes.runs.knowledge.list_runs", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.runs.knowledge.count_runs", lambda db, **kw: 0)
    monkeypatch.setattr("kubo.api.routes.sources.knowledge.sources_with_stats", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.entities.knowledge.list_entities", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.entities.knowledge.count_entities", lambda db, **kw: 0)
    _session_ctx = SimpleNamespace(
        tenant_id=RecordID("tenant", "breakglass"),
        user_id=RecordID("user", "breakglass-owner"),
        role="owner",
    )
    for _mod in ("entities", "dashboard", "distilled", "flows"):
        monkeypatch.setattr(
            f"kubo.api.routes.{_mod}.resolve_session",
            lambda request, db, _ctx=_session_ctx: _ctx,
        )
    monkeypatch.setattr("kubo.api.routes.entities._entity_in_tenant", lambda db, eid, ctx: True)
    monkeypatch.setattr("kubo.api.routes.dispatches.knowledge.list_dispatches", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.dispatches.knowledge.count_dispatches", lambda db, **kw: 0)
    _OWNER = Destination(
        id=RecordID("destination", "owner"),
        name="Renato (Telegram)",
        kind="pessoa",
        channel="telegram",
        address="1",
        enabled=True,
        archived_at=None,
    )
    monkeypatch.setattr(
        "kubo.api.routes.destinations.destination_store.list_destinations",
        lambda db, **kw: [_OWNER],
    )
    monkeypatch.setattr(
        "kubo.api.routes.destinations.destination_store.active_destinations",
        lambda db, **kw: [_OWNER],
    )
    monkeypatch.setattr(
        "kubo.api.routes.destinations.invite_store.list_invites", lambda db, **kw: []
    )
    _settings_store_stub = SimpleNamespace(
        get_settings=lambda db: Settings(
            id=RecordID("settings", "global"),
            digest_cron="30 9 * * *",
            distribution_paused=False,
            default_destination=None,
        )
    )
    monkeypatch.setattr("kubo.api.routes.destinations.settings_store", _settings_store_stub)


@pytest.fixture(autouse=True)
def ui_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Env mínimo para a fábrica subir; devolve a senha em claro para os testes de login."""
    monkeypatch.setenv("KUBO_PASSWORD_HASH", hash_password(UI_PASSWORD))
    monkeypatch.setenv(
        "SESSION_SECRET",
        "test-session-secret-0123456789abcdef",  # pragma: allowlist secret
    )
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "kubo_notify_bot")
    monkeypatch.setenv(
        "KUBO_TELEGRAM_WEBHOOK_SECRET",
        "test-webhook-secret",  # pragma: allowlist secret
    )
    monkeypatch.setenv("KUBO_FIREBASE_PROJECT_ID", "kubo-test-project")
    monkeypatch.setenv("KUBO_FIREBASE_OWNER_UIDS", "")
    monkeypatch.setenv("KUBO_FIREBASE_API_KEY", "test-firebase-api-key")  # pragma: allowlist secret
    monkeypatch.setenv(
        "KUBO_BREAKGLASS_TENANT_ID",
        "tenant:breakglass",  # pragma: allowlist secret
    )
    monkeypatch.setenv(
        "KUBO_BREAKGLASS_USER_ID",
        "user:breakglass-owner",  # pragma: allowlist secret
    )
    monkeypatch.delenv("KUBO_ALLOWED_HOSTS", raising=False)
    return UI_PASSWORD


@pytest.fixture
def client() -> TestClient:
    """Client anônimo (sem sessão). base_url=https para o cookie Secure ser aceito
    pelo jar do TestClient (ADR-0035). raise_server_exceptions garante que um 500 real
    da app apareça como erro, não seja mascarado."""
    return TestClient(create_app(), base_url="https://testserver")


@pytest.fixture
def authed_client(client: TestClient) -> TestClient:
    """Client já autenticado: faz o POST /login com a senha certa e mantém o cookie."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    return client
