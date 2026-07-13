"""Testes da tela de Destinos (12.8, paridade DestinosScreen): auth guard + render
dos artefatos configurados (do schedules.yaml) e dos destinos (do destinations.yaml).
Lê os YAML reais do repo (config declarativa, sem banco)."""

from __future__ import annotations

from starlette.testclient import TestClient

from kubo.api.routes.destinations import _humanize_cron


def test_destinations_requires_auth(client: TestClient) -> None:
    """Sem sessão, a tela redireciona pro login (guard antes de tudo)."""
    assert client.get("/destinations", follow_redirects=False).status_code == 303


def test_destinations_renders_owner_and_digest_artefato(authed_client: TestClient) -> None:
    """A tela mostra o destino do dono (do destinations.yaml) e o artefato Digest com
    a agenda humana (do schedules.yaml)."""
    html = authed_client.get("/destinations").text
    assert "Renato (Telegram)" in html
    assert "telegram" in html
    assert "Digest" in html
    assert "diário às 09:30" in html


def test_humanize_cron_daily() -> None:
    """Cron diário `M H * * *` vira 'diário às HH:MM'; não-diário devolve o cru."""
    assert _humanize_cron("30 9 * * *") == "diário às 09:30"
    assert _humanize_cron("0 8 * * *") == "diário às 08:00"
    assert _humanize_cron("*/5 * * * *") == "*/5 * * * *"
