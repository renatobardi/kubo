"""Testes da tela de Envios (12.7, paridade EnviosScreen): auth guard, estado
vazio, render de artefato/canal/destino/status, erro estruturado expansível.
Store mockada — teste de rota."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kubo.store.knowledge import DispatchListItem


def _dispatch(**kw: object) -> DispatchListItem:
    """DispatchListItem com defaults — os testes sobrescrevem só o que importa."""
    base: dict[str, object] = {
        "channel": "telegram",
        "destination": "owner-telegram",
        "status": "ok",
        "item_count": 3,
        "error_kind": None,
        "error": None,
        "sent_at": "2026-07-13T09:30:00+00:00",
    }
    base.update(kw)
    return DispatchListItem(**base)  # type: ignore[arg-type]


def test_dispatches_requires_auth(client: TestClient) -> None:
    """Sem sessão, a tela redireciona pro login (guard antes do banco)."""
    assert client.get("/dispatches", follow_redirects=False).status_code == 303


def test_dispatches_empty_state(authed_client: TestClient) -> None:
    """Sem envios (stub padrão), estado vazio, 200."""
    resp = authed_client.get("/dispatches")
    assert resp.status_code == 200
    assert "Nenhum envio ainda" in resp.text


def test_dispatches_renders_channel_destination_and_count(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linha mostra artefato (Digest), canal, destino e o nº de destilados."""
    monkeypatch.setattr(
        "kubo.api.routes.dispatches.knowledge.list_dispatches",
        lambda db, **kw: [
            _dispatch(channel="telegram", destination="owner-telegram", item_count=3)
        ],
    )
    monkeypatch.setattr("kubo.api.routes.dispatches.knowledge.count_dispatches", lambda db, **kw: 1)
    html = authed_client.get("/dispatches").text
    assert "Digest" in html
    assert "telegram" in html
    assert "owner-telegram" in html
    assert "3 destilados" in html


def test_dispatches_error_is_expandable(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Envio com falha mostra o badge do kind e o erro estruturado no painel."""
    monkeypatch.setattr(
        "kubo.api.routes.dispatches.knowledge.list_dispatches",
        lambda db, **kw: [
            _dispatch(
                status="error",
                error_kind="telegram_send",
                error={"kind": "telegram_send", "message": "Telegram respondeu HTTP 400"},
            )
        ],
    )
    monkeypatch.setattr("kubo.api.routes.dispatches.knowledge.count_dispatches", lambda db, **kw: 1)
    html = authed_client.get("/dispatches").text
    assert "telegram_send" in html
    assert "Telegram respondeu HTTP 400" in html
    assert "<details" in html
