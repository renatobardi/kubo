"""Fixtures da suíte da UI: env de auth (autouse) + clients (anônimo e autenticado).

A fábrica passou a exigir `KUBO_PASSWORD_HASH` + `SESSION_SECRET` (fail-fast do
ADR-0014); `ui_env` é autouse para que qualquer `create_app()` na suíte tenha o
env mínimo. `KUBO_ALLOWED_HOSTS` fica sem setar de propósito: o default inclui
`testserver`, o Host que o TestClient envia."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient

from kubo.api.app import create_app
from kubo.api.auth import hash_password
from kubo.store.knowledge import DashboardCounts

UI_PASSWORD = "test-ui-password"  # pragma: allowlist secret


@contextmanager
def _fake_connect(cfg: Any = None) -> Any:
    """Conexão falsa: as leituras da store estão stubadas, o db é ignorado."""
    yield object()


@pytest.fixture(autouse=True)
def stub_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desacopla as rotas de página do banco real: connect falso + leituras vazias
    por padrão. Testes com dados específicos (test_distilled, test_dashboard)
    sobrescrevem estas leituras no corpo do teste."""
    for mod in ("dashboard", "distilled", "runs"):
        monkeypatch.setattr(f"kubo.api.routes.{mod}.client.connect", _fake_connect)
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.knowledge.dashboard_counts",
        lambda db: DashboardCounts(distilled=0, items=0, sources=0),
    )
    monkeypatch.setattr("kubo.api.routes.dashboard.knowledge.recent_runs", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.distilled.knowledge.list_distilled", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.runs.knowledge.list_runs", lambda db, **kw: [])


@pytest.fixture(autouse=True)
def ui_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Env mínimo para a fábrica subir; devolve a senha em claro para os testes de login."""
    monkeypatch.setenv("KUBO_PASSWORD_HASH", hash_password(UI_PASSWORD))
    monkeypatch.setenv(
        "SESSION_SECRET",
        "test-session-secret-0123456789abcdef",  # pragma: allowlist secret
    )
    monkeypatch.delenv("KUBO_ALLOWED_HOSTS", raising=False)
    return UI_PASSWORD


@pytest.fixture
def client() -> TestClient:
    """Client anônimo (sem sessão). raise_server_exceptions garante que um 500 real
    da app apareça como erro, não seja mascarado."""
    return TestClient(create_app())


@pytest.fixture
def authed_client(client: TestClient) -> TestClient:
    """Client já autenticado: faz o POST /login com a senha certa e mantém o cookie."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    return client
