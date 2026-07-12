"""Testes do Painel (9.7): contagens + últimas execuções discriminadas por error.kind."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kubo.store.knowledge import DashboardCounts, RunSummary


def test_dashboard_renders_counts_and_runs(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O Painel mostra as contagens do acervo e as últimas runs, com o error.kind
    da falha visível (discriminação da mini-sessão pós-M6)."""
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.knowledge.dashboard_counts",
        lambda db: DashboardCounts(distilled=42, items=100, sources=7),
    )
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.knowledge.recent_runs",
        lambda db, **kw: [
            RunSummary(
                worker="distiller",
                status="error",
                error_kind="rate_limit",
                started_at="2026-07-12T09:00:00Z",
                finished_at="2026-07-12T09:00:05Z",
            ),
            RunSummary(
                worker="feed",
                status="ok",
                error_kind=None,
                started_at="2026-07-12T08:00:00Z",
                finished_at="2026-07-12T08:00:02Z",
            ),
        ],
    )
    html = authed_client.get("/").text
    assert "42" in html and "100" in html and "7" in html
    assert "distiller" in html
    assert "rate_limit" in html  # falha discriminada pelo kind
    assert "feed" in html


def test_dashboard_empty_state(authed_client: TestClient) -> None:
    """Sem runs (stub padrão do conftest), o Painel mostra o estado vazio, 200."""
    resp = authed_client.get("/")
    assert resp.status_code == 200
    assert "Nenhuma execução" in resp.text


def test_dashboard_requires_auth(client: TestClient) -> None:
    """Sem sessão, o Painel redireciona pro login (o guard atua antes do banco)."""
    assert client.get("/", follow_redirects=False).status_code == 303


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
