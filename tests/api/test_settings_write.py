"""Escrita de Configurações pela rota /settings (KUBO-44, molde ADR-0018).

Integração: SurrealDB real + usuário kubo_rw EDITOR + app FastAPI real.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.app import create_app
from kubo.store import client, destinations, migrations
from kubo.store import settings as settings_store
from kubo.store.client import connect as _real_connect
from tests.api.conftest import UI_PASSWORD

pytestmark = pytest.mark.integration

_DB = "test_settings_write"
_RW_PASS = secrets.token_urlsafe(24)


def _real_get_settings(db: Any) -> settings_store.Settings | None:
    """Recupera o settings real usando conexão real (conftest stuba a função global)."""
    _rid = RecordID("settings", "global")
    rows = db.query("SELECT * FROM $r;", {"r": _rid})
    if not rows:
        return None
    row = rows[0]
    default = row.get("default_destination")
    return settings_store.Settings(
        id=row["id"],
        digest_cron=row["digest_cron"],
        distribution_paused=bool(row["distribution_paused"]),
        default_destination=default if default is not None else None,
    )


@pytest.fixture
def app_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """App real apontado a um db efêmero com kubo_rw."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.setattr("kubo.store.client.connect", _real_connect)
    monkeypatch.setattr("kubo.store.settings.get_settings", _real_get_settings)
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
    """Autentica e devolve (client, csrf) lido do form de Configurações."""
    tc = TestClient(app)
    login = tc.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert login.status_code == 303
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', tc.get("/settings").text)
    assert m, "csrf ausente no form de Configurações"
    return tc, m.group(1)


def _settings_row(db_name: str = _DB) -> dict[str, Any] | None:
    """Lê COMO ROOT a linha settings:global."""
    with _real_connect(replace(client.config(), database=db_name)) as root:
        rows = root.query("SELECT * FROM settings:global;")
        return rows[0] if rows else None


def test_settings_requires_auth(client: TestClient) -> None:
    """Sem sessão, /settings redireciona pro login."""
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_settings_page_shows_current_values(app_db: Any) -> None:
    """A tela exibe o cron e o destino padrão salvos no banco."""
    tc, _ = _login_csrf(app_db)
    with _real_connect(replace(client.config(), database=_DB)) as root:
        dest_rid = destinations.create_destination(
            root, name="Renato", kind="pessoa", channel="telegram", address="123"
        )
        settings_store.put_settings(
            root,
            digest_cron="0 10 * * *",
            distribution_paused=True,
            default_destination=dest_rid,
        )

    html = tc.get("/settings").text

    assert "0 10 * * *" in html
    assert "Renato" in html
    assert 'value="on" checked' in html or 'checked value="on"' in html or "checked" in html


def test_update_settings_changes_cron_and_pause(app_db: Any) -> None:
    """POST /settings persiste cron, pausa e destino padrão."""
    tc, csrf = _login_csrf(app_db)
    with _real_connect(replace(client.config(), database=_DB)) as root:
        dest_rid = destinations.create_destination(
            root, name="Renato", kind="pessoa", channel="telegram", address="123"
        )

    resp = tc.post(
        "/settings",
        data={
            "digest_cron": "0 20 * * *",
            "distribution_paused": "on",
            "default_destination": str(dest_rid.id),
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    row = _settings_row()
    assert row is not None
    assert row["digest_cron"] == "0 20 * * *"
    assert row["distribution_paused"] is True
    assert str(row["default_destination"]) == str(dest_rid)


def test_update_settings_rejects_invalid_cron(app_db: Any) -> None:
    """Cron malformado volta 400 e não toca o banco."""
    tc, csrf = _login_csrf(app_db)
    with _real_connect(replace(client.config(), database=_DB)) as root:
        settings_store.put_settings(
            root,
            digest_cron="30 9 * * *",
            distribution_paused=False,
            default_destination=None,
        )

    resp = tc.post(
        "/settings",
        data={
            "digest_cron": "não é cron",
            "distribution_paused": "",
            "default_destination": "",
            "csrf": csrf,
        },
        follow_redirects=False,
    )

    assert resp.status_code == 400
    row = _settings_row()
    assert row is not None
    assert row["digest_cron"] == "30 9 * * *"


def test_update_settings_rejects_bad_csrf(app_db: Any) -> None:
    """CSRF errado é 403, sem escrita."""
    tc, _ = _login_csrf(app_db)
    resp = tc.post(
        "/settings",
        data={"digest_cron": "0 20 * * *", "csrf": "bad"},
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert _settings_row() is None
