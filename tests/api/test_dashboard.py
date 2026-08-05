"""Testes do Painel (9.7): contagens + últimas execuções discriminadas por error.kind."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.knowledge import DashboardCounts, RunSummary
from kubo.store.study import Lesson, Topic

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")


def _lesson(*, is_placeholder: bool = False) -> Lesson:
    """Lesson fake para testes do card D9."""
    return Lesson(
        id=RecordID("lesson", "l1"),
        tenant_id=_TENANT,
        user_id=_USER,
        study_plan=RecordID("study_plan", "p1"),
        plan_entry=RecordID("plan_entry", "e1"),
        scheduled_for=datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc),
        concept="",
        scenario="",
        application="",
        recap=None,
        quiz=[],
        provenance=[],
        is_placeholder=is_placeholder,
    )


def _topic(*, state: str = "running") -> Topic:
    """Topic fake para testes do card D9."""
    return Topic(
        id=RecordID("topic", "t1"),
        tenant_id=_TENANT,
        user_id=_USER,
        title="Estudo de Agentic Coding",
        state=state,
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )


def test_dashboard_renders_counts_and_runs(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O Painel mostra as contagens do acervo e as últimas runs, com o error.kind
    da falha visível (discriminação da mini-sessão pós-M6)."""
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.knowledge.dashboard_counts",
        lambda db, **kw: DashboardCounts(distilled=42, items=100, sources=7, entities=13),
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
        lambda db, **kw: DashboardCounts(distilled=1, items=2, sources=3, entities=4),
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


# --- Card "Lição de hoje" (D9) ----------------------------------------------------------


def test_dashboard_today_lesson_card_present(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Com lesson_for_today retornando (lesson, topic), o card aparece no Painel."""
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.study_store.lesson_for_today",
        lambda db, **kw: (_lesson(), _topic()),
    )
    html = authed_client.get("/").text
    assert "Lição de hoje" in html
    assert "Estudo de Agentic Coding" in html
    assert "/study/topics/t1/lessons/l1" in html


def test_dashboard_today_lesson_card_absent_when_none(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem lesson_for_today (None), o card não aparece."""
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.study_store.lesson_for_today", lambda db, **kw: None
    )
    html = authed_client.get("/").text
    assert "Lição de hoje" not in html


def test_dashboard_today_lesson_card_placeholder(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lição placeholder mostra mensagem de scheduler em vez de data."""
    monkeypatch.setattr(
        "kubo.api.routes.dashboard.study_store.lesson_for_today",
        lambda db, **kw: (_lesson(is_placeholder=True), _topic(state="scheduled")),
    )
    html = authed_client.get("/").text
    assert "Sendo gerada pelo scheduler" in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
