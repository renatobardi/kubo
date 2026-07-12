"""Testes da tela de Fontes (M3, paridade `FontesScreen.jsx`): lista com kind, itens
acumulados e badge de recência factual (E4). Sem detalhe e sem 'Adicionar fonte' (E1).
Store mockada — teste de rota."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.knowledge import SourceStat


def _src(**kw: object) -> SourceStat:
    base: dict[str, object] = {
        "id": RecordID("source", "s1"),
        "canonical": "https://x/feed",
        "kind": "rss",
        "title": None,
        "items": 0,
        "last_collected_at": None,
    }
    base.update(kw)
    return SourceStat(**base)  # type: ignore[arg-type]


def _iso_days_ago(days: int) -> str:
    return str(datetime.now(timezone.utc) - timedelta(days=days))


def test_sources_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert client.get("/sources", follow_redirects=False).status_code == 303


def test_sources_empty_state(authed_client: TestClient) -> None:
    """Sem fontes (stub padrão), estado vazio, 200."""
    resp = authed_client.get("/sources")
    assert resp.status_code == 200
    assert "Nenhuma fonte" in resp.text


def test_sources_lists_kind_items_and_recency(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linha mostra nome, kind, itens acumulados e o badge de recência factual (E4)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [
            _src(
                canonical="https://y/@canal",
                kind="youtube",
                title="Canal X",
                items=42,
                last_collected_at=_iso_days_ago(3),
            )
        ],
    )
    html = authed_client.get("/sources").text
    assert "Canal X" in html
    assert "youtube" in html
    assert "42 itens" in html
    assert "há 3d" in html  # recência factual


def test_sources_badge_sem_coleta_when_never_collected(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fonte sem coleta nenhuma → badge 'sem coleta' (E4: 2º estado)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [_src(last_collected_at=None, items=0)],
    )
    assert "sem coleta" in authed_client.get("/sources").text


def test_sources_no_add_button(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """'Adicionar fonte' fica FORA (E1: backend inexistente, desvio declarado)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [_src(items=1, last_collected_at=_iso_days_ago(0))],
    )
    assert "Adicionar fonte" not in authed_client.get("/sources").text


def test_sources_orders_collected_before_never(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fontes que coletaram vêm antes das que nunca coletaram (eixo de recência)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [
            _src(canonical="never://src", last_collected_at=None),
            _src(canonical="fresh://src", last_collected_at=_iso_days_ago(1)),
        ],
    )
    html = authed_client.get("/sources").text
    assert html.index("fresh://src") < html.index("never://src")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
