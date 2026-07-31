"""API tests for team invite and workspace switch (KUBO-120)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import respx
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.firebase_tokens import clear_jwks_cache
from kubo.errors import TeamInviteError
from tests.api._firebase_test_helpers import (
    _JWKS_URL,
    _decode_session_cookie,
    _firebase_token,
    rsa_keypair,
)

_FAKE_BREAKGLASS_TENANT_ID = "tenant:breakglass"
_FAKE_INVITE_TENANT_ID = "tenant:team-a"
_FAKE_USER_ID = "user:owner-a"
_FAKE_INVITE_TOKEN = "invite-token-123"


@pytest.fixture(autouse=True)
def stub_writer_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escrita de auth usa `connect_rw` (ADR-0018); no unit não há KUBO_RW_SURREAL_PASS,
    então o stub entra ANTES de `rw_config()` explodir — 503 de env ausente é caso do
    `test_sources.py`, não deste arquivo. O stub NÃO pode viver na conftest: o objeto
    `client` é o MESMO de todas as rotas e envenenaria os 503 de fontes."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.auth.client.connect_rw", _fake_connect)


def _fake_user(*, uid: str, user_id: str = _FAKE_USER_ID) -> Any:
    """Minimal user object for auth route mocks."""
    return SimpleNamespace(
        id=RecordID("user", user_id),
        firebase_uid=uid,
        email=f"{uid}@example.com",
    )


def _fake_membership(
    tenant_id: str = _FAKE_BREAKGLASS_TENANT_ID,
    user_id: str = _FAKE_USER_ID,
    role: str = "owner",
) -> Any:
    return SimpleNamespace(
        user=RecordID("user", user_id),
        tenant=RecordID("tenant", tenant_id.split(":", 1)[1]),
        role=role,
    )


def test_create_invite_requires_login(client: TestClient) -> None:
    """POST /auth/invite without a session redirects to /login."""
    resp = client.post("/auth/invite", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_create_invite_generates_token_and_link(
    authed_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated owner creates an invite and gets back a token + link."""
    called = {}

    def _fake_get_user_by_firebase_uid(db: Any, firebase_uid: str) -> Any:
        called["uid"] = firebase_uid
        return _fake_user(uid=firebase_uid)

    def _fake_list_memberships(db: Any, user: RecordID) -> list[Any]:
        called["memberships_user"] = str(user)
        return [_fake_membership()]

    def _fake_create_invite(db: Any, *, tenant_id: RecordID, created_by: RecordID) -> Any:
        called["create"] = (str(tenant_id), str(created_by))
        return SimpleNamespace(
            id=RecordID("team_invite", "abc"),
            tenant_id=tenant_id,
            token=_FAKE_INVITE_TOKEN,
            role="member",
        )

    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_user_by_firebase_uid",
        _fake_get_user_by_firebase_uid,
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.list_memberships_for_user",
        _fake_list_memberships,
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.team_invites_store.create_team_invite",
        _fake_create_invite,
    )

    resp = authed_client.post("/auth/invite", follow_redirects=False)
    assert resp.status_code == 200
    assert _FAKE_INVITE_TOKEN in resp.text
    assert "/invite/" in resp.text
    assert called.get("create") is not None


def test_firebase_login_with_invite_creates_member(
    respx_mock: respx.MockRouter,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Login via invite link creates a member membership in the existing tenant."""
    clear_jwks_cache()
    private_pem, jwk = rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid="new-member-uid")

    called = {}

    def _fake_get_user(db: Any, firebase_uid: str) -> Any | None:
        called["get_user"] = firebase_uid
        return None

    def _fake_create_user(db: Any, *, firebase_uid: str, email: str | None = None) -> Any:
        called["create_user"] = (firebase_uid, email)
        return _fake_user(uid=firebase_uid, user_id="newmember")

    def _fake_accept_invite(db: Any, *, token: str, user_id: RecordID) -> Any:
        called["accept"] = (token, str(user_id))
        return SimpleNamespace(
            id=RecordID("team_invite", "abc"),
            tenant_id=RecordID("tenant", "team-a"),
            role="member",
        )

    def _fake_get_tenant(db: Any, tenant_id: RecordID) -> Any:
        called["get_tenant"] = str(tenant_id)
        return SimpleNamespace(id=tenant_id)

    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_user_by_firebase_uid",
        _fake_get_user,
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.create_user",
        _fake_create_user,
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_tenant",
        _fake_get_tenant,
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.team_invites_store.accept_team_invite",
        _fake_accept_invite,
    )

    resp = client.post(
        f"/auth/firebase?invite={_FAKE_INVITE_TOKEN}",
        json={"id_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    session = _decode_session_cookie(resp.headers["set-cookie"])
    assert session["role"] == "member"
    assert session["tenant_id"] == _FAKE_INVITE_TENANT_ID
    assert session["uid"] == "new-member-uid"
    assert called["accept"] == (_FAKE_INVITE_TOKEN, "user:newmember")


def test_firebase_login_with_invalid_invite_is_401(
    respx_mock: respx.MockRouter,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid/expired/used invite is 401 with the login form, never a session."""
    clear_jwks_cache()
    private_pem, jwk = rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid="new-member-uid")

    def _reject(db: Any, *, token: str, user_id: RecordID) -> Any:
        raise TeamInviteError("team invite is invalid, expired, or already used")

    monkeypatch.setattr(
        "kubo.api.routes.auth.team_invites_store.accept_team_invite",
        _reject,
    )

    resp = client.post(
        f"/auth/firebase?invite={_FAKE_INVITE_TOKEN}",
        json={"id_token": token},
        follow_redirects=False,
    )

    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    assert "Convite inválido" in resp.text


def test_firebase_login_without_invite_ignores_invite_flow(
    respx_mock: respx.MockRouter,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal login (no invite) still creates a new owner tenant via get_or_create."""
    clear_jwks_cache()
    private_pem, jwk = rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid="new-member-uid")

    called = {}

    def _fake_get_or_create(
        db: Any, *, firebase_uid: str, email: str | None = None
    ) -> tuple[Any, Any]:
        called["get_or_create"] = (firebase_uid, email)
        user = _fake_user(uid=firebase_uid, user_id=firebase_uid.replace(":", ""))
        tenant = SimpleNamespace(id=RecordID("tenant", f"tenant-{firebase_uid}"))
        return user, tenant

    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_or_create_user_and_tenant",
        _fake_get_or_create,
    )

    resp = client.post("/auth/firebase", json={"id_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert called["get_or_create"] is not None


def test_workspace_switch_changes_session_tenant(
    authed_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /auth/switch changes the active session tenant to an allowed one."""
    called = {}

    def _fake_get_user(db: Any, firebase_uid: str) -> Any:
        called["uid"] = firebase_uid
        return _fake_user(uid=firebase_uid)

    def _fake_list_memberships(db: Any, user: RecordID) -> list[Any]:
        return [
            _fake_membership(tenant_id="tenant:team-a", role="owner"),
            _fake_membership(tenant_id="tenant:team-b", role="member"),
        ]

    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_user_by_firebase_uid",
        _fake_get_user,
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.list_memberships_for_user",
        _fake_list_memberships,
    )

    resp = authed_client.post(
        "/auth/switch",
        data={"tenant_id": "tenant:team-b", "next": "/dashboard"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"

    session = _decode_session_cookie(resp.headers["set-cookie"])
    assert session["tenant_id"] == "tenant:team-b"
    assert session["role"] == "member"


def test_workspace_switch_rejects_unauthorized_tenant(
    authed_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /auth/switch is rejected for a tenant without membership."""

    def _fake_get_user(db: Any, firebase_uid: str) -> Any:
        return _fake_user(uid=firebase_uid)

    def _fake_list_memberships(db: Any, user: RecordID) -> list[Any]:
        return [_fake_membership(tenant_id="tenant:team-a", role="owner")]

    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_user_by_firebase_uid",
        _fake_get_user,
    )
    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.list_memberships_for_user",
        _fake_list_memberships,
    )

    resp = authed_client.post(
        "/auth/switch",
        data={"tenant_id": "tenant:team-c"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_workspace_switch_rejects_malformed_tenant_id(
    authed_client: TestClient,
) -> None:
    """POST /auth/switch returns 4xx for a malformed tenant_id."""
    resp = authed_client.post(
        "/auth/switch",
        data={"tenant_id": "not-a-record"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
