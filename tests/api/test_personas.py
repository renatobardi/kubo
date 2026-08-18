"""UI de catálogo de personas (KUBO-221 Fátias 2/3).

Unit: rotas de /personas com stores stubadas pelo conftest.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from tests.api.conftest import UI_PASSWORD


@pytest.fixture
def authed_client_with_personas(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Client autenticado; personas retornam valores conhecidos para testes."""
    _personas = [
        {
            "name": "distiller-score",
            "executor": "api",
            "model": "anthropic/claude-haiku-4-5",
            "prompt": "",
            "permissions": [],
            "max_tokens": 16384,
            "temperature": 0.0,
            "reasoning_effort": None,
            "timeout": 60.0,
            "max_turns": None,
        },
        {
            "name": "distiller-distill",
            "executor": "api",
            "model": "anthropic/claude-haiku-4-5",
            "prompt": "",
            "permissions": [],
            "max_tokens": 16384,
            "temperature": 0.0,
            "reasoning_effort": None,
            "timeout": 60.0,
            "max_turns": None,
        },
        {
            "name": "distiller-day-summary",
            "executor": "api",
            "model": "anthropic/claude-haiku-4-5",
            "prompt": "",
            "permissions": [],
            "max_tokens": 16384,
            "temperature": 0.0,
            "reasoning_effort": None,
            "timeout": 60.0,
            "max_turns": None,
        },
    ]

    def _list(_session: Any) -> list[dict[str, Any]]:
        return _personas

    def _get(_session: Any, *, name: str) -> dict[str, Any] | None:
        for p in _personas:
            if p["name"] == name:
                return p
        return None

    def _upsert(_session: Any, *, persona: dict[str, Any]) -> dict[str, Any]:
        for i, p in enumerate(_personas):
            if p["name"] == persona["name"]:
                _personas[i] = {**p, **persona}
                return _personas[i]
        _personas.append(persona)
        return persona

    @contextmanager
    def _fake_connect_rw() -> Iterator[Any]:
        yield object()

    monkeypatch.setattr("kubo.api.routes.personas.client.connect_rw", _fake_connect_rw)
    monkeypatch.setattr("kubo.api.routes.personas.catalog_store.list_personas", _list)
    monkeypatch.setattr("kubo.api.routes.personas.catalog_store.get_persona", _get)
    monkeypatch.setattr("kubo.api.routes.personas.catalog_store.upsert_persona", _upsert)
    monkeypatch.setattr("kubo.api.routes.personas.verify_csrf", lambda _request, _token: True)
    monkeypatch.setattr(
        "kubo.api.routes.personas.resolve_session",
        lambda _request, _db: SimpleNamespace(
            tenant_id=RecordID("tenant", "breakglass"),
            user_id=RecordID("user", "breakglass-owner"),
            role="owner",
        ),
    )
    client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    yield client


def test_personas_page_requires_login(client: TestClient) -> None:
    """Sem sessão, /personas redireciona para login."""
    assert client.get("/personas", follow_redirects=False).status_code == 303


def test_personas_page_lists_distiller_personas(authed_client_with_personas: TestClient) -> None:
    """A tela lista as personas do destilador com modelo e limite de tokens."""
    resp = authed_client_with_personas.get("/personas")
    assert resp.status_code == 200
    html = resp.text
    assert "distiller-score" in html
    assert "distiller-distill" in html
    assert "distiller-day-summary" in html
    assert "anthropic/claude-haiku-4-5" in html
    assert "16384" in html


def test_update_persona_changes_model_and_persists(authed_client_with_personas: TestClient) -> None:
    """Owner edita o modelo de uma persona; o valor persiste e redireciona."""
    tc = authed_client_with_personas
    # csrf vem do form de /personas
    html = tc.get("/personas").text
    from html.parser import HTMLParser

    class CsrfParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.value = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "input":
                attr = dict(attrs)
                if attr.get("name") == "csrf" and attr.get("value"):
                    self.value = attr["value"]

    parser = CsrfParser()
    parser.feed(html)
    csrf = parser.value
    assert csrf

    resp = tc.post(
        "/personas/distiller-score",
        data={
            "csrf": csrf,
            "model": "anthropic/claude-sonnet-5",
            "max_tokens": "8192",
            "temperature": "0.5",
            "reasoning_effort": "",
            "timeout": "120",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/personas"

    list_resp = tc.get("/personas")
    assert "anthropic/claude-sonnet-5" in list_resp.text
    assert "8192" in list_resp.text


def test_member_cannot_update_persona(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Membro vê a lista, mas POST retorna 403."""

    @contextmanager
    def _fake_connect_rw() -> Iterator[Any]:
        yield object()

    monkeypatch.setattr("kubo.api.routes.personas.client.connect_rw", _fake_connect_rw)
    monkeypatch.setattr("kubo.api.routes.personas.verify_csrf", lambda _request, _token: True)
    monkeypatch.setattr(
        "kubo.api.routes.personas.resolve_session",
        lambda _request, _db: SimpleNamespace(
            tenant_id=RecordID("tenant", "breakglass"),
            user_id=RecordID("user", "breakglass-member"),
            role="member",
        ),
    )
    client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    resp = client.post(
        "/personas/distiller-score",
        data={
            "csrf": "whatever",
            "model": "anthropic/claude-sonnet-5",
            "max_tokens": "8192",
            "temperature": "0.5",
            "reasoning_effort": "",
            "timeout": "120",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 403


@contextmanager
def _exploding_connect_rw() -> Iterator[Any]:
    """Context manager falso que simula kubo_rw indisponível."""
    from kubo.errors import ConfigError

    raise ConfigError("kubo_rw ausente")
    yield  # noqa: UP038 (nunca executa; precisa ser gerador para @contextmanager)


def test_write_unavailable_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem kubo_rw configurado, a rota de escrita degrada para 503."""
    monkeypatch.setattr("kubo.api.routes.personas.verify_csrf", lambda _request, _token: True)
    monkeypatch.setattr(
        "kubo.api.routes.personas.client.connect_rw",
        _exploding_connect_rw,
    )
    monkeypatch.setattr(
        "kubo.api.routes.personas.resolve_session",
        lambda _request, _db: SimpleNamespace(
            tenant_id=RecordID("tenant", "breakglass"),
            user_id=RecordID("user", "breakglass-owner"),
            role="owner",
        ),
    )
    client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    resp = client.post(
        "/personas/distiller-score",
        data={
            "csrf": "whatever",
            "model": "anthropic/claude-sonnet-5",
            "max_tokens": "8192",
            "temperature": "0.5",
            "reasoning_effort": "",
            "timeout": "120",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 503
