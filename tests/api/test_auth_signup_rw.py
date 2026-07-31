"""Self-signup Firebase precisa escrever com kubo_rw, não com a credencial read-only.

Integração no molde de `test_settings_write.py`, com a diferença que reproduz a classe
do bug de produção: `client.connect()` é ligado a um usuário Surreal **VIEWER de verdade**
(`kubo_ro_test`), como o `kubo_ro` do servidor (ADR-0018). Com a credencial de leitura o
SurrealDB engole o CREATE em silêncio — o teste só passa se a rota abrir `connect_rw`.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Any

import pytest
import respx
from starlette.testclient import TestClient

from kubo.api.app import create_app
from kubo.api.firebase_tokens import clear_jwks_cache
from kubo.store import client, migrations, tenancy
from kubo.store.client import Config
from kubo.store.client import connect as _real_connect
from tests.api._firebase_test_helpers import _JWKS_URL, _firebase_token, rsa_keypair

pytestmark = pytest.mark.integration

_DB = "test_auth_signup"
_RW_PASS = secrets.token_urlsafe(24)
_RO_USER = "kubo_ro_test"
_RO_PASS = secrets.token_urlsafe(24)
_SIGNUP_UID = "signup-google-uid"
_SIGNUP_EMAIL = "novo@example.com"

# A conftest da suíte da UI stuba estas leituras/escritas no objeto de módulo
# `kubo.store.tenancy` (o mesmo que `routes.auth.tenancy_store`); aqui elas precisam
# ser as reais, senão o self-signup nunca toca o banco.
_real_get_user_by_firebase_uid = tenancy.get_user_by_firebase_uid
_real_create_user = tenancy.create_user
_real_list_memberships = tenancy.list_memberships_for_user
_real_get_tenant = tenancy.get_tenant
_real_list_tenants = tenancy.list_tenants


def _ro_connect(cfg: Config | None = None) -> AbstractContextManager[Any]:
    """`connect()` como em produção: credencial VIEWER (read-only).

    Com `cfg` explícito — o caminho que `connect_rw()` usa — delega sem trocar a
    credencial, para que a escrita continue chegando ao kubo_rw EDITOR.
    """
    if cfg is None:
        cfg = replace(client.config(), user=_RO_USER, password=_RO_PASS)
    return _real_connect(cfg)


@pytest.fixture
def signup_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """App real, db efêmero, kubo_rw EDITOR para escrita e kubo_ro_test VIEWER na leitura."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.setattr("kubo.store.client.connect", _ro_connect)
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
        root.query(f"DEFINE USER OVERWRITE kubo_rw ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")
        root.query(f"DEFINE USER OVERWRITE {_RO_USER} ON ROOT PASSWORD '{_RO_PASS}' ROLES VIEWER;")
        try:
            yield create_app()
        finally:
            root.query("REMOVE USER IF EXISTS kubo_rw ON ROOT;")
            root.query(f"REMOVE USER IF EXISTS {_RO_USER} ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _sign_in(app: Any, respx_mock: respx.MockRouter, *, uid: str, email: str) -> Any:
    """POST /auth/firebase com um ID token válido (JWKS mockado)."""
    clear_jwks_cache()
    private_pem, jwk = rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid=uid, email=email)
    # raise_server_exceptions=False: um 500 do handler vira resposta, e a falha do teste
    # fica legível como status errado em vez de exceção não capturada.
    tc = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    return tc.post("/auth/firebase", json={"id_token": token}, follow_redirects=False)


def _rows(query: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Lê COMO ROOT (read-back independente da credencial usada pela rota)."""
    with _real_connect(replace(client.config(), database=_DB)) as root:
        return root.query(query, params) if params else root.query(query)


def test_firebase_self_signup_persists_user_tenant_and_membership(
    signup_app: Any, respx_mock: respx.MockRouter
) -> None:
    """Self-signup com credencial de leitura no `connect()`: a rota ainda grava tudo."""
    resp = _sign_in(signup_app, respx_mock, uid=_SIGNUP_UID, email=_SIGNUP_EMAIL)

    assert resp.status_code == 303

    users = _rows("SELECT * FROM user WHERE firebase_uid = $uid;", {"uid": _SIGNUP_UID})
    assert users, "user do self-signup não foi criado (escrita caiu na conexão read-only)"
    assert users[0]["email"] == _SIGNUP_EMAIL

    tenants = _rows("SELECT * FROM tenant;")
    assert len(tenants) == 1, "self-signup deveria criar exatamente um tenant"

    memberships = _rows("SELECT * FROM membership WHERE in = $u;", {"u": users[0]["id"]})
    assert memberships, "membership owner do self-signup não foi criada"
    assert memberships[0]["out"] == tenants[0]["id"]
    assert memberships[0]["role"] == "owner"


def test_firebase_self_signup_without_writer_credential_is_503(
    signup_app: Any, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-fast do molde ADR-0018: sem kubo_rw a escrita é indisponível (503), não 500."""
    monkeypatch.delenv("KUBO_RW_SURREAL_PASS", raising=False)

    resp = _sign_in(signup_app, respx_mock, uid=_SIGNUP_UID, email=_SIGNUP_EMAIL)

    assert resp.status_code == 503
    assert not _rows("SELECT * FROM user WHERE firebase_uid = $uid;", {"uid": _SIGNUP_UID})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
