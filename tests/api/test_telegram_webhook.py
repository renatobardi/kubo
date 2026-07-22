"""Webhook inbound do Telegram (KUBO-62/69): aceite de convite por `/start <token>`.

Integração: SurrealDB real + usuário kubo_rw EDITOR efêmero + app FastAPI real.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient

from kubo.api.app import create_app
from kubo.store import client, destinations, invites, migrations
from kubo.store.client import connect as _real_connect

pytestmark = pytest.mark.integration

_DB = "test_telegram_webhook"
_RW_PASS = secrets.token_urlsafe(24)  # gerada por run — nunca literal no repo (invariante 8)
_SECRET = "test-webhook-secret"  # pragma: allowlist secret
_BOT_USERNAME = "kubo_notify_bot"


@pytest.fixture
def app_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """App real apontado a um db efêmero com kubo_rw."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", _BOT_USERNAME)
    monkeypatch.setenv("KUBO_TELEGRAM_WEBHOOK_SECRET", _SECRET)
    # Restaura conexão REAL: a conftest stuba client.connect por default.
    monkeypatch.setattr("kubo.store.client.connect", _real_connect)
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


def _telegram_update(chat_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1,
            "text": text,
        },
    }


def test_webhook_missing_secret_returns_401(app_db: Any) -> None:
    """Header de secret ausente/incorreto vira 401 imediatamente."""
    tc = TestClient(app_db)
    resp = tc.post("/telegram/webhook", json=_telegram_update(123, "/start abc"))
    assert resp.status_code == 401


def test_webhook_valid_start_accepts_invite(app_db: Any) -> None:
    """`/start <token>` correto cria o destination e marca o convite aceito."""
    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.use(root_cfg.namespace, root_cfg.database)
        invite = invites.create_invite(root, name="Marina")

    tc = TestClient(app_db)
    resp = tc.post(
        "/telegram/webhook",
        json=_telegram_update(123456, f"/start {invite.token}"),
        headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    with _real_connect(root_cfg) as root:
        root.use(root_cfg.namespace, root_cfg.database)
        updated = invites.get_invite(root, invite.id)
        assert updated is not None
        assert updated.accepted_at is not None

        # A store retorna o destination_id; verificamos pela presença do endereço.
        rows = root.query(
            "SELECT * FROM destination WHERE channel = 'telegram' AND address = '123456';"
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "Marina"


def test_webhook_invalid_token_returns_200(app_db: Any) -> None:
    """Token inexistente/expirado/aceito não gera 500 — Telegram recebe 200."""
    tc = TestClient(app_db)
    resp = tc.post(
        "/telegram/webhook",
        json=_telegram_update(123456, "/start naoexiste"),
        headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_webhook_duplicate_chat_id_returns_200(app_db: Any) -> None:
    """chat_id já cadastrado não explode: loga e devolve 200."""
    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.use(root_cfg.namespace, root_cfg.database)
        destinations.create_destination(
            root, name="Dono", kind="pessoa", channel="telegram", address="123456"
        )
        invite = invites.create_invite(root, name="Marina")

    tc = TestClient(app_db)
    resp = tc.post(
        "/telegram/webhook",
        json=_telegram_update(123456, f"/start {invite.token}"),
        headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
