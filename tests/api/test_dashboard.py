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
        lambda db: DashboardCounts(distilled=42, items=100, sources=7, entities=13),
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
    assert "42" in html and "100" in html and "7" in html and "13" in html  # inclui entidades
    assert "distiller" in html
    # rate_limit vira badge NEUTRO 'quota' também no Painel (E6, consistente com Execuções):
    # apresentação, sem reclassificar o status='error'. O kind cru fica na tela de Execuções.
    assert "quota" in html
    assert "feed" in html


def test_dashboard_stat_tiles_are_clickable_links(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrofit M5: os 4 StatTiles navegam — Fontes→/sources, Itens→/runs,
    Destilados→/distilled, Entidades→/entities (paridade HomeScreen)."""
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.knowledge.dashboard_counts",
        lambda db: DashboardCounts(distilled=1, items=2, sources=3, entities=4),
    )
    html = authed_client.get("/").text
    for route in ('href="/sources"', 'href="/runs"', 'href="/distilled"', 'href="/entities"'):
        assert route in html


def test_dashboard_card_has_ver_todas_action(authed_client: TestClient) -> None:
    """O card 'Últimas execuções' tem a ação 'Ver todas' apontando pra /runs (paridade)."""
    html = authed_client.get("/").text
    assert "Ver todas" in html
    assert 'href="/runs"' in html


def test_dashboard_omits_gate_alert_and_flows(authed_client: TestClient) -> None:
    """Gate alert e 'Fluxos ativos' ficam FORA (desvios declarados: backend inexistente)."""
    html = authed_client.get("/").text
    assert "decisão aguardando" not in html  # gate alert do mockup
    assert "Fluxos ativos" not in html


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
