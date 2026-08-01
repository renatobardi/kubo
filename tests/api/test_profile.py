"""Testes da UI de perfil (KUBO-150).

Testes unitários com stubs de store/conexão, usando o conftest da UI.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store import tenancy as _tenancy

_CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')

_PROFILE_USER = SimpleNamespace(
    id=RecordID("user", "breakglass-owner"),
    firebase_uid="user:breakglass-owner",
    email="renato@example.com",
    work_context=None,
)

_PROFILE = SimpleNamespace(
    display_name="Renato",
    language="pt-BR",
    timezone="America/Sao_Paulo",
)

_MEMBERSHIP = SimpleNamespace(
    tenant=RecordID("tenant", "breakglass"),
    role="owner",
    theme="system",
)


def _update_profile(
    db: object, *, user_id: object, display_name: str, language: str, timezone: str
) -> SimpleNamespace:
    text = display_name.strip()
    if not text or len(text) > 64:
        raise _tenancy.StoreError("display_name must be 1-64 characters")
    _PROFILE.display_name = display_name
    _PROFILE.language = language
    _PROFILE.timezone = timezone
    return _PROFILE


def _update_theme(db: object, *, user_id: object, tenant_id: object, theme: str) -> SimpleNamespace:
    if theme not in {"light", "dark", "system"}:
        raise _tenancy.StoreError("theme must be 'light', 'dark' or 'system'")
    _MEMBERSHIP.theme = theme
    return _MEMBERSHIP


@pytest.fixture(autouse=True)
def _profile_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs das funções de store e connect_rw que a rota de perfil chama.

    `connect_rw` é stubada localmente aqui (não no conftest autouse) para não
    vazar para outras rotas — `kubo.store.client` é módulo compartilhado.
    """

    @contextmanager
    def _fake_rw(_cfg: Any = None) -> Any:
        yield object()

    monkeypatch.setattr("kubo.api.routes.profile.client.connect_rw", _fake_rw)
    monkeypatch.setattr("kubo.store.tenancy.get_user", lambda db, user_id: _PROFILE_USER)
    monkeypatch.setattr("kubo.store.tenancy.get_user_profile", lambda db, user_id: _PROFILE)
    monkeypatch.setattr(
        "kubo.store.tenancy.get_membership",
        lambda db, *, user_id, tenant_id: _MEMBERSHIP,
    )
    monkeypatch.setattr("kubo.store.tenancy.update_user_profile", _update_profile)
    monkeypatch.setattr("kubo.store.tenancy.update_membership_theme", _update_theme)


def _csrf(client: TestClient) -> str:
    """Pega o token CSRF da tela de perfil."""
    resp = client.get("/profile")
    assert resp.status_code == 200
    match = _CSRF_RE.search(resp.text)
    assert match is not None
    return match.group(1)


def test_get_profile_renders(authed_client: TestClient) -> None:
    """A tela de perfil mostra nome, idioma, timezone, tema e avatar."""
    resp = authed_client.get("/profile")

    assert resp.status_code == 200
    assert "Renato" in resp.text
    assert "pt-BR" in resp.text
    assert "America/Sao_Paulo" in resp.text
    assert 'value="system"' in resp.text
    assert "gravatar.com" in resp.text


def test_post_profile_updates_and_redirects(authed_client: TestClient) -> None:
    """POST /profile salva e redireciona de volta."""
    resp = authed_client.post(
        "/profile",
        data={
            "csrf": _csrf(authed_client),
            "display_name": "Bardi",
            "language": "en-US",
            "timezone": "UTC",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/profile"


def test_post_profile_rejects_empty_display_name(authed_client: TestClient) -> None:
    """Nome vazio gera re-render com aviso de validação."""
    resp = authed_client.post(
        "/profile",
        data={
            "csrf": _csrf(authed_client),
            "display_name": "   ",
            "language": "pt-BR",
            "timezone": "America/Sao_Paulo",
        },
    )

    assert resp.status_code == 400
    assert "display_name" in resp.text.lower()


def test_post_membership_preferences_updates_theme(authed_client: TestClient) -> None:
    """POST /membership/preferences salva o tema e redireciona."""
    resp = authed_client.post(
        "/membership/preferences",
        data={
            "csrf": _csrf(authed_client),
            "theme": "dark",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/profile"


def test_post_membership_preferences_rejects_invalid_theme(authed_client: TestClient) -> None:
    """Tema inválido gera 400."""
    resp = authed_client.post(
        "/membership/preferences",
        data={
            "csrf": _csrf(authed_client),
            "theme": "blue",
        },
    )

    assert resp.status_code == 400


def test_post_profile_requires_csrf(authed_client: TestClient) -> None:
    """POST sem CSRF é recusado."""
    resp = authed_client.post(
        "/profile",
        data={
            "display_name": "Bardi",
            "language": "en-US",
            "timezone": "UTC",
        },
    )

    assert resp.status_code == 403


def test_get_profile_avatar_with_missing_email(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the user has no email, the avatar is computed from an empty string (md5 of "")."""
    no_email_user = SimpleNamespace(
        id=_PROFILE_USER.id,
        firebase_uid=_PROFILE_USER.firebase_uid,
        email=None,
        work_context=None,
    )
    monkeypatch.setattr("kubo.store.tenancy.get_user", lambda db, user_id: no_email_user)

    import hashlib

    expected_digest = hashlib.sha256(b"").hexdigest()
    resp = authed_client.get("/profile")

    assert resp.status_code == 200
    assert expected_digest in resp.text
    assert "gravatar.com" in resp.text
