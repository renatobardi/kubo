"""Convites de destino Telegram pela rota real (KUBO-58/68/70): criar, listar,
reenviar e envio de e-mail.

Integração: SurrealDB real + usuário kubo_rw EDITOR efêmero + app FastAPI real.
"""

from __future__ import annotations

import importlib
import re
import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient

from kubo.api.app import create_app
from kubo.errors import SenderError
from kubo.store import client, migrations
from kubo.store.client import connect as _real_connect
from tests.api.conftest import UI_PASSWORD

pytestmark = pytest.mark.integration

_DB = "test_destinations_invites"
_RW_PASS = secrets.token_urlsafe(24)  # gerada por run — nunca literal no repo (invariante 8)


@pytest.fixture
def app_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """App real apontado a um db efêmero com kubo_rw."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "kubo_notify_bot")
    monkeypatch.setenv("KUBO_TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    # Restaura conexão REAL: a conftest stuba client.connect por default.
    monkeypatch.setattr("kubo.store.client.connect", _real_connect)
    # A conftest stuba também list_* em destinations/invites/settings; recarrega os
    # módulos para restaurar as funções reais (os módulos são os mesmos da rota).
    importlib.reload(importlib.import_module("kubo.store.invites"))
    importlib.reload(importlib.import_module("kubo.store.destinations"))
    importlib.reload(importlib.import_module("kubo.store.settings"))
    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        root.use(root_cfg.namespace, root_cfg.database)
        migrations.apply_migrations(root)
        root.query(f"DEFINE USER OVERWRITE kubo_rw ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")
        try:
            yield create_app()
        finally:
            root.query("REMOVE USER IF EXISTS kubo_rw ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _login_csrf(app: Any) -> tuple[TestClient, str]:
    """Autentica e devolve (client, csrf) lido do form de Convite."""
    tc = TestClient(app)
    login = tc.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert login.status_code == 303
    html = tc.get("/destinations").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form"
    return tc, m.group(1)


def test_create_invite_without_email_redirects_and_shows_link(app_db: Any) -> None:
    """Criar convite sem e-mail redireciona e a lista mostra o link copiável."""
    tc, csrf = _login_csrf(app_db)

    resp = tc.post(
        "/destinations/invites",
        data={"name": "Marina", "email": "", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    html = tc.get("/destinations").text
    assert "Marina" in html
    assert "https://t.me/kubo_notify_bot?start=" in html
    assert "Convites pendentes" in html


def test_create_invite_with_email_sends_email_and_shows_chip(
    app_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Convite com e-mail dispara o sender e exibe o chip 'com e-mail'."""
    tc, csrf = _login_csrf(app_db)
    calls: list[dict[str, Any]] = []

    def fake_send(email: str, name: str, token: str) -> None:
        calls.append({"email": email, "name": name, "token": token})

    monkeypatch.setattr("kubo.api.routes.destinations._send_invite_email", fake_send)

    resp = tc.post(
        "/destinations/invites",
        data={"name": "Marina", "email": "marina@exemplo.com", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["email"] == "marina@exemplo.com"
    assert calls[0]["name"] == "Marina"
    assert len(calls[0]["token"]) == 32

    html = tc.get("/destinations").text
    assert "com e-mail" in html


def test_create_invite_email_failure_still_creates_and_shows_link(
    app_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha no envio de e-mail mantém o convite criado e mostra o link na tela."""
    tc, csrf = _login_csrf(app_db)

    def _fail(_email: str, _name: str, _token: str) -> None:
        raise SenderError("SMTP down")

    monkeypatch.setattr("kubo.api.routes.destinations._send_invite_email", _fail)

    resp = tc.post(
        "/destinations/invites",
        data={"name": "Marina", "email": "marina@exemplo.com", "csrf": csrf},
    )
    assert resp.status_code == 200
    assert "https://t.me/kubo_notify_bot?start=" in resp.text


def test_resend_rejects_non_expired_invite(app_db: Any) -> None:
    """Reenviar convite pendente é rejeitado com aviso na tela."""
    tc, csrf = _login_csrf(app_db)
    tc.post(
        "/destinations/invites",
        data={"name": "Marina", "email": "", "csrf": csrf},
        follow_redirects=False,
    )
    html = tc.get("/destinations").text
    m = re.search(r'action="/destinations/invites/([^/]+)/resend"', html)
    assert m, "resend action não encontrado"
    iid = m.group(1)

    resp = tc.post(
        f"/destinations/invites/{iid}/resend",
        data={"csrf": csrf},
    )
    assert resp.status_code == 409
    assert "ainda não expirou" in resp.text


def test_resend_expired_invite_updates_token_and_sends_email(
    app_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reenviar convite expirado gera token novo e reenvia e-mail."""
    tc, csrf = _login_csrf(app_db)
    tc.post(
        "/destinations/invites",
        data={"name": "Marina", "email": "marina@exemplo.com", "csrf": csrf},
        follow_redirects=False,
    )

    # Expira o convite direto no banco.
    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.use(root_cfg.namespace, root_cfg.database)
        root.query("UPDATE invite SET expires_at = time::now() - 1s WHERE name = 'Marina';")

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "kubo.api.routes.destinations._send_invite_email",
        lambda email, name, token: calls.append({"email": email, "name": name, "token": token}),
    )

    html = tc.get("/destinations").text
    m = re.search(r'action="/destinations/invites/([^/]+)/resend"', html)
    assert m
    iid = m.group(1)

    resp = tc.post(
        f"/destinations/invites/{iid}/resend",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["email"] == "marina@exemplo.com"
    assert len(calls[0]["token"]) == 32
