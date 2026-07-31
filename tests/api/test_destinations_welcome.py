"""Rota de boas-vindas manual por destino (welcome)."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.app import create_app
from kubo.api.session import resolve_session as _real_resolve_session
from kubo.distribution.email import SmtpConfig
from kubo.store import client, destinations, migrations, tenancy
from kubo.store.client import connect as _real_connect
from tests.api.conftest import UI_PASSWORD

pytestmark = pytest.mark.integration

_DB = "test_destinations_welcome"
_RW_PASS = secrets.token_urlsafe(24)
_BREAKGLASS_UID = "user:breakglass-owner"

# Captura implementações reais antes do conftest stubar.
_real_get_user_by_firebase_uid = tenancy.get_user_by_firebase_uid
_real_create_user = tenancy.create_user
_real_list_memberships = tenancy.list_memberships_for_user
_real_get_tenant = tenancy.get_tenant
_real_list_tenants = tenancy.list_tenants


@pytest.fixture
def app_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """App real + db efêmero com user/tenant/membership reais para resolve_session."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.setattr("kubo.store.client.connect", _real_connect)
    # Desstubar resolve_session e tenancy para o welcome gravar dispatch real.
    monkeypatch.setattr("kubo.api.routes.destinations.resolve_session", _real_resolve_session)
    monkeypatch.setattr(
        "kubo.store.tenancy.get_user_by_firebase_uid", _real_get_user_by_firebase_uid
    )
    monkeypatch.setattr("kubo.store.tenancy.create_user", _real_create_user)
    monkeypatch.setattr("kubo.store.tenancy.list_memberships_for_user", _real_list_memberships)
    monkeypatch.setattr("kubo.store.tenancy.get_tenant", _real_get_tenant)
    monkeypatch.setattr("kubo.store.tenancy.list_tenants", _real_list_tenants)
    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        root.use(root_cfg.namespace, root_cfg.database)
        migrations.apply_migrations(root)
        user = tenancy.create_user(root, firebase_uid=_BREAKGLASS_UID)
        tenant = tenancy.create_tenant(root, name="Welcome Test", owner_user_id=user.id)
        monkeypatch.setenv("KUBO_BREAKGLASS_USER_ID", _BREAKGLASS_UID)
        monkeypatch.setenv("KUBO_BREAKGLASS_TENANT_ID", str(tenant.id))
        root.query(f"DEFINE USER OVERWRITE kubo_rw ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")
        try:
            yield create_app()
        finally:
            root.query("REMOVE USER IF EXISTS kubo_rw ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _login_csrf(app: Any) -> tuple[TestClient, str]:
    """Autentica e devolve (client, csrf) lido do form de destinos."""
    tc = TestClient(app, base_url="https://testserver")
    login = tc.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert login.status_code == 303
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', tc.get("/destinations").text)
    assert m, "csrf ausente no form de Destinos"
    return tc, m.group(1)


def test_welcome_sends_telegram(app_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /destinations/{id}/welcome envia mensagem de boas-vindas no Telegram."""
    tc, csrf = _login_csrf(app_db)
    tenant = RecordID(*os.environ["KUBO_BREAKGLASS_TENANT_ID"].split(":"))
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rid = destinations.create_destination(
            root,
            name="Renato Bardi",
            kind="pessoa",
            channel="telegram",
            address="123456",
            tenant_id=tenant,
        )
    did = rid.id

    calls: list[dict[str, Any]] = []

    def fake_send(*, token: str, chat_id: str, text: str, **_: Any) -> None:
        calls.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("kubo.distribution.telegram.send_telegram", fake_send)
    monkeypatch.setattr("kubo.distribution.telegram.telegram_token", lambda: "bot-token")

    resp = tc.post(f"/destinations/{did}/welcome", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/destinations"
    assert len(calls) == 1
    assert calls[0]["token"] == "bot-token"
    assert calls[0]["chat_id"] == "123456"
    assert "Obrigado" in calls[0]["text"]
    assert "Bardi" in calls[0]["text"]

    # Audit: welcome registra um dispatch com artifact="welcome"
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rows = root.query(
            "SELECT artifact, status, channel, item_count FROM dispatch WHERE destination = $dest;",
            {"dest": rid},
        )
    assert len(rows) == 1
    assert rows[0]["artifact"] == "welcome"
    assert rows[0]["status"] == "ok"
    assert rows[0]["channel"] == "telegram"
    assert rows[0]["item_count"] == 0


def test_welcome_sends_email(app_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /destinations/{id}/welcome envia mensagem de boas-vindas por e-mail."""
    tc, csrf = _login_csrf(app_db)
    tenant = RecordID(*os.environ["KUBO_BREAKGLASS_TENANT_ID"].split(":"))
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rid = destinations.create_destination(
            root,
            name="Claudia",
            kind="pessoa",
            channel="email",
            address="claudia@example.com",
            tenant_id=tenant,
        )
    did = rid.id

    calls: list[dict[str, Any]] = []

    def fake_send_email(
        *, to: str, subject: str, text_body: str, html_body: str, smtp_config: Any
    ) -> None:
        calls.append(
            {
                "to": to,
                "subject": subject,
                "text_body": text_body,
                "html_body": html_body,
            }
        )

    monkeypatch.setattr("kubo.distribution.email.send_email", fake_send_email)
    monkeypatch.setattr(
        "kubo.distribution.email.email_smtp_config",
        lambda: SmtpConfig(
            host="smtp.example.com",
            port=587,
            user="bot",
            password="x",
            from_address="bot@example.com",
        ),
    )

    resp = tc.post(f"/destinations/{did}/welcome", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/destinations"
    assert len(calls) == 1
    assert calls[0]["to"] == "claudia@example.com"
    assert calls[0]["subject"] == "Bem-vindo ao Kubo"
    assert "Bardi" in calls[0]["text_body"]
    assert "Bardi" in calls[0]["html_body"]

    # Audit: welcome registra um dispatch com artifact="welcome"
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rows = root.query(
            "SELECT artifact, status, channel FROM dispatch WHERE destination = $dest;",
            {"dest": rid},
        )
    assert len(rows) == 1
    assert rows[0]["artifact"] == "welcome"
    assert rows[0]["status"] == "ok"
    assert rows[0]["channel"] == "email"
