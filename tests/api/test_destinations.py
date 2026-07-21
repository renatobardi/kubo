"""Destinations screen tests (12.8, DestinosScreen parity): auth guard + rendering of
configured artifacts (settings + active DB destinations) and destinations (DB)."""

from __future__ import annotations

from starlette.testclient import TestClient

from kubo.api.routes.destinations import _humanize_cron


def test_destinations_requires_auth(client: TestClient) -> None:
    """Without a session, the screen redirects to login (guard before everything)."""
    assert client.get("/destinations", follow_redirects=False).status_code == 303


def test_destinations_renders_owner_and_digest_artefato(authed_client: TestClient) -> None:
    """The screen shows the owner destination (from DB stub) and the Digest artifact with
    the human agenda (from settings digest_cron)."""
    html = authed_client.get("/destinations").text
    assert "Renato (Telegram)" in html
    assert "telegram" in html
    assert "Digest" in html
    assert "diário às 09:30" in html


def test_humanize_cron_daily() -> None:
    """Daily cron `M H * * *` becomes 'diário às HH:MM'; non-daily returns the raw cron."""
    assert _humanize_cron("30 9 * * *") == "diário às 09:30"
    assert _humanize_cron("0 8 * * *") == "diário às 08:00"
    assert _humanize_cron("*/5 * * * *") == "*/5 * * * *"
