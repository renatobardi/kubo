"""Descoberta e validação assistida de fonte RSS (KUBO-50) — testes unitários.

Mockam HTTP com respx, o LLM com um finder fake e o guard SSRF com monkeypatch.
Não tocam SurrealDB (testes de UI/escrita em test_sources*.py)."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from starlette.testclient import TestClient

_VALID_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Feed Title</title>
<link>https://example.com/</link>
<item>
<title>Entry One</title>
<link>https://example.com/1</link>
<guid>guid-1</guid>
</item>
</channel>
</rss>
"""

_SITE_WITH_RSS = (
    b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'
)

_SITE_WITH_FEED = (
    b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed"></head></html>'
)


class _FakeFinder:
    """Finder fake: devolve a URL pré-configurada e registra os nomes recebidos."""

    def __init__(self, url: str | None) -> None:
        self._url = url
        self.calls: list[str] = []

    def guess(self, name: str) -> str | None:
        self.calls.append(name)
        return self._url


@pytest.fixture(autouse=True)
def _bypass_feed_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita DNS real no guard SSRF dos testes de rota (o guard é testado em test_feed.py)."""
    monkeypatch.setattr("kubo.workers.feed._reject_non_global_ip", lambda _host: None)


def _csrf(client: TestClient) -> str:
    """Lê o token CSRF do form da lista de fontes."""
    html = client.get("/sources").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente na página de fontes"
    return m.group(1)


def _set_finder(monkeypatch: pytest.MonkeyPatch, url: str | None) -> _FakeFinder:
    """Substitui o finder da rota por um fake."""
    fake = _FakeFinder(url)
    monkeypatch.setattr("kubo.api.routes.sources.get_finder", lambda: fake)
    return fake


def _mock_get(url: str, content: bytes, status: int = 200) -> None:
    """Registra mock respx por igualdade exata de URL (evita matching por host)."""
    respx.route(method="GET", url__eq=url).mock(
        return_value=httpx.Response(status, content=content)
    )


@respx.mock
def test_test_feed_url_valid_returns_preview(authed_client: TestClient) -> None:
    """Modo feed: URL válida retorna preview com título, URL descoberta e amostra."""
    feed_url = "https://feed.example/rss"
    _mock_get(feed_url, _VALID_FEED)
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/sources/test",
        data={"mode": "feed", "canonical": feed_url, "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Feed encontrado" in resp.text
    assert feed_url in resp.text
    assert "Feed Title" in resp.text
    assert "Entry One" in resp.text


@respx.mock
def test_test_feed_url_http_error_returns_failure(authed_client: TestClient) -> None:
    """Modo feed: HTTP 503 vira falha soft no snippet HTMX."""
    feed_url = "https://feed.example/rss"
    _mock_get(feed_url, b"", status=503)
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/sources/test",
        data={"mode": "feed", "canonical": feed_url, "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Não achei um feed válido" in resp.text
    assert "URL do feed" in resp.text


@respx.mock
def test_test_site_with_link_discovers_feed(
    authed_client: TestClient,
) -> None:
    """Modo site: autodiscovery encontra <link rel='alternate'> e valida o feed."""
    site_url = "https://site.example"
    feed_url = "https://site.example/rss"
    html = _SITE_WITH_RSS
    _mock_get(site_url, html)
    _mock_get(feed_url, _VALID_FEED)
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/sources/test",
        data={"mode": "site", "canonical": site_url, "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Feed encontrado" in resp.text
    assert feed_url in resp.text
    assert "autodiscovery" in resp.text


@respx.mock
def test_test_site_without_link_returns_failure(authed_client: TestClient) -> None:
    """Modo site: página sem feed declarado retorna falha de autodiscovery."""
    site_url = "https://site.example"
    html = b"<html><head><title>No feed</title></head></html>"
    _mock_get(site_url, html)
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/sources/test",
        data={"mode": "site", "canonical": site_url, "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Autodiscovery no site" in resp.text
    assert "sem" in resp.text


@respx.mock
def test_test_name_finder_guess_succeeds(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Modo nome: finder acerta e o feed responde — retorna via IA."""
    feed_url = "https://finder.example/feed"
    fake = _set_finder(monkeypatch, feed_url)
    _mock_get(feed_url, _VALID_FEED)
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/sources/test",
        data={"mode": "name", "canonical": "FinderCo", "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Feed encontrado" in resp.text
    assert feed_url in resp.text
    assert "IA (finder)" in resp.text
    assert fake.calls == ["FinderCo"]


@respx.mock
def test_test_name_finder_misses_but_autodiscovery_saves(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Modo nome: chute do finder falha, mas autodiscovery no domínio acha o feed."""
    wrong = "https://finder.example/wrong"
    fake = _set_finder(monkeypatch, wrong)
    site_html = _SITE_WITH_FEED
    _mock_get(wrong, b"", status=404)
    _mock_get("https://finder.example", site_html)
    _mock_get("https://finder.example/feed", _VALID_FEED)
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/sources/test",
        data={"mode": "name", "canonical": "FinderCo", "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Feed encontrado" in resp.text
    assert "https://finder.example/feed" in resp.text
    assert "autodiscovery" in resp.text
    assert fake.calls == ["FinderCo"]


@respx.mock
def test_test_name_total_failure(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Modo nome: finder chuta e domínio também não tem feed — falha em cadeia."""
    wrong = "https://finder.example/wrong"
    _set_finder(monkeypatch, wrong)
    site_html = b"<html><head><title>No feed</title></head></html>"
    _mock_get(wrong, b"", status=404)
    _mock_get("https://finder.example", site_html)
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/sources/test",
        data={"mode": "name", "canonical": "FinderCo", "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Não achei um feed válido" in resp.text
    assert "IA (finder)" in resp.text


@respx.mock
def test_test_requires_csrf(authed_client: TestClient) -> None:
    """POST /sources/test sem CSRF é recusado antes de qualquer fetch."""
    _mock_get("https://x/feed", _VALID_FEED)
    resp = authed_client.post(
        "/sources/test",
        data={"mode": "feed", "canonical": "https://x/feed"},
    )

    assert resp.status_code == 403


def test_test_invalid_mode_returns_validation_error(authed_client: TestClient) -> None:
    """Modo fora do conjunto vira falha de entrada na borda pydantic."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources/test",
        data={"mode": "bogus", "canonical": "https://x/feed", "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Entrada inválida" in resp.text


def test_test_empty_value_returns_validation_error(authed_client: TestClient) -> None:
    """Valor vazio é rejeitado na borda pydantic."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources/test",
        data={"mode": "feed", "canonical": "   ", "csrf": csrf},
    )

    assert resp.status_code == 200
    assert "Entrada inválida" in resp.text
