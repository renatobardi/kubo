"""Testes de auth de browser (9.2): guard, login, logout, TrustedHost, fail-fast."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import respx
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.app import create_app
from kubo.api.firebase_tokens import clear_jwks_cache
from kubo.errors import ConfigError
from tests.api._firebase_test_helpers import (
    _JWKS_URL,
    _decode_session_cookie,
    _firebase_token,
    rsa_keypair,
)
from tests.api.conftest import UI_PASSWORD

# Valor incorreto para o teste de rejeição — não é credencial, só "senha errada".
_WRONG_LOGIN = "nope"


@pytest.fixture(autouse=True)
def stub_writer_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escrita de auth usa `connect_rw` (ADR-0018); no unit não há KUBO_RW_SURREAL_PASS,
    então o stub entra ANTES de `rw_config()` explodir — 503 de env ausente é caso do
    `test_sources.py`, não deste arquivo. O stub NÃO pode viver na conftest: o objeto
    `client` é o MESMO de todas as rotas e envenenaria os 503 de fontes."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.auth.client.connect_rw", _fake_connect)


def test_login_page_is_public(client: TestClient) -> None:
    """GET /login não exige sessão (200, mostra o form e os botões Firebase)."""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()
    text = resp.text
    assert "Entrar com Google" in text or "firebase" in text.lower()
    # Config do Firebase (apiKey/projectId) está embutida como JSON escapado.
    assert "kubo-test-project" in text


def test_firebase_config_renders_camel_case_in_login(client: TestClient) -> None:
    """Firebase JS SDK exige config camelCase (apiKey, authDomain, projectId)."""
    resp = client.get("/login")
    assert resp.status_code == 200
    text = resp.text
    assert '"apiKey"' in text
    assert '"authDomain"' in text
    assert '"projectId"' in text


def test_protected_route_redirects_anonymous_to_login(client: TestClient) -> None:
    """Sem sessão, uma rota protegida redireciona (303) para /login."""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_healthz_stays_public_under_auth(client: TestClient) -> None:
    """/healthz continua fora do guard mesmo com auth ligado."""
    assert client.get("/healthz").status_code == 200


def test_static_stays_public_under_auth(client: TestClient) -> None:
    """/static continua servível sem sessão (CSS/JS carregam na tela de login)."""
    assert client.get("/static/htmx-2.0.4.min.js").status_code == 200


def test_login_success_opens_session(client: TestClient) -> None:
    """Senha certa: 303 para / e a rota protegida passa a responder 200."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_login_wrong_password_denied(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Senha errada: não abre sessão e devolve 401 (sleep-on-fail neutralizado no teste)."""
    monkeypatch.setattr("kubo.api.routes.auth.time.sleep", lambda _s: None)
    resp = client.post("/login", data={"password": _WRONG_LOGIN}, follow_redirects=False)
    assert resp.status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303


def test_logout_clears_session(authed_client: TestClient) -> None:
    """Logout encerra a sessão: depois dele a rota protegida volta a redirecionar."""
    assert authed_client.get("/").status_code == 200
    resp = authed_client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert authed_client.get("/", follow_redirects=False).status_code == 303


def test_session_cookie_is_secure_httponly_and_lax(client: TestClient) -> None:
    """O cookie de sessão é Secure + HttpOnly + SameSite=Lax (ADR-0035/0036)."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    set_cookie = resp.headers["set-cookie"].lower()
    assert "secure" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_session_carries_role_owner_and_uid(client: TestClient) -> None:
    """Login scrypt grava sessão com breakglass user/tenant configurado."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    session = _decode_session_cookie(resp.headers["set-cookie"])
    assert session["role"] == "owner"
    assert session["uid"] == "user:breakglass-owner"
    assert session["tenant_id"] == "tenant:breakglass"
    assert "auth_at" in session


def test_session_regenerates_on_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Login limpa a sessão anterior (session fixation) e grava role/uid/auth_at."""
    resp1 = client.get("/login")
    assert "set-cookie" not in resp1.headers

    call_count = 0

    def _fake_time() -> float:
        nonlocal call_count
        call_count += 1
        return 1000.0 + call_count

    monkeypatch.setattr("kubo.api.routes.auth.time.time", _fake_time)
    resp2 = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    cookie1 = resp2.headers["set-cookie"]
    assert client.get("/").status_code == 200
    # Re-login após logout deve produzir cookie diferente (conteúdo muda por auth_at).
    client.post("/logout", follow_redirects=False)
    resp3 = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    cookie2 = resp3.headers["set-cookie"]
    assert cookie1 != cookie2


def test_request_recognizes_https_behind_proxy(client: TestClient) -> None:
    """Com base_url=https (proxy TLS), o app recebe o cookie Secure nas requisições autenticadas."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    assert "secure" in resp.headers["set-cookie"].lower()
    # Requisição seguinte envia o cookie (TestClient base_url=https simula HTTPS).
    resp2 = client.get("/")
    assert resp2.status_code == 200
    assert "kubo_session" in resp2.request.headers.get("cookie", "")


def test_trusted_host_rejects_unknown_host(client: TestClient) -> None:
    """Host fora da allowlist é barrado (400) — fecha DNS rebinding."""
    resp = client.get("/healthz", headers={"host": "evil.example.com"})
    assert resp.status_code == 400


def test_kubo_oute_pro_is_allowed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """kubo.oute.pro é aceito pelo TrustedHost quando configurado (ADR-0035)."""
    monkeypatch.setenv("KUBO_ALLOWED_HOSTS", "kubo.oute.pro")
    secure_client = TestClient(create_app(), base_url="https://kubo.oute.pro")
    assert secure_client.get("/healthz").status_code == 200


def test_login_rejects_concurrent_attempt_fast(client: TestClient) -> None:
    """Com uma tentativa de login já em voo (gate tomado), a próxima é recusada na hora
    (429) — sem gastar scrypt/sleep nem prender uma thread do pool. Fecha o self-DoS e
    torna o rate-limit real (uma tentativa por vez), não teatro de sleep sequencial."""
    from kubo.api.routes.auth import _LOGIN_GATE

    assert _LOGIN_GATE.acquire(blocking=False) is True
    try:
        resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
        assert resp.status_code == 429
    finally:
        _LOGIN_GATE.release()


def test_create_app_fails_fast_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem KUBO_PASSWORD_HASH / SESSION_SECRET a fábrica recusa subir (invariante 8)."""
    monkeypatch.delenv("KUBO_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ConfigError):
        create_app()


# UID usado pelos testes de login Firebase (KUBO-93).
_FIREBASE_OWNER_UID = "owner-google-uid"


def _fake_user_and_tenant(uid: str) -> tuple[Any, Any]:
    """Devolve objetos mínimos que a rota /auth/firebase espera de get_or_create_user_and_tenant."""
    safe_id = "".join(c for c in uid if c.isalnum())
    user = SimpleNamespace(
        id=RecordID("user", uid),
        firebase_uid=uid,
        email=f"{uid}@example.com",
    )
    tenant = SimpleNamespace(id=RecordID("tenant", f"tenant{safe_id}"))
    return user, tenant


def test_firebase_login_success(
    respx_mock: respx.MockRouter,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token Firebase válido abre sessão owner + tenant_id e redireciona para o Painel."""
    clear_jwks_cache()
    private_pem, jwk = rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid=_FIREBASE_OWNER_UID)

    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_or_create_user_and_tenant",
        lambda db, *, firebase_uid, email=None: _fake_user_and_tenant(firebase_uid),
    )

    resp = client.post("/auth/firebase", json={"id_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert client.get("/").status_code == 200

    session = _decode_session_cookie(resp.headers["set-cookie"])
    assert session["role"] == "owner"
    assert session["uid"] == _FIREBASE_OWNER_UID
    assert session["tenant_id"] == "tenant:tenantownergoogleuid"


def test_firebase_login_allows_unknown_uid_and_creates_tenant(
    respx_mock: respx.MockRouter,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-signup: uid novo é aceito e a rota cria user/tenant via store."""
    clear_jwks_cache()
    private_pem, jwk = rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid="new-user-uid", email="owner@example.com")

    called = {}

    def _fake_get_or_create(
        db: Any, *, firebase_uid: str, email: str | None = None
    ) -> tuple[Any, Any]:
        called["uid"] = firebase_uid
        called["email"] = email
        return _fake_user_and_tenant(firebase_uid)

    monkeypatch.setattr(
        "kubo.api.routes.auth.tenancy_store.get_or_create_user_and_tenant",
        _fake_get_or_create,
    )

    resp = client.post("/auth/firebase", json={"id_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert called["uid"] == "new-user-uid"
    assert called["email"] == "owner@example.com"

    session = _decode_session_cookie(resp.headers["set-cookie"])
    assert session["role"] == "owner"
    assert session["tenant_id"] == "tenant:tenantnewuseruid"


def test_firebase_login_superadmin_gets_superadmin_role(
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UID na allowlist de superadmin abre sessão com role=superadmin, sem criar tenant."""
    clear_jwks_cache()
    private_pem, jwk = rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid="owner-google-uid")

    monkeypatch.setenv("KUBO_FIREBASE_OWNER_UIDS", "owner-google-uid")
    configured_client = TestClient(create_app(), base_url="https://testserver")

    resp = configured_client.post(
        "/auth/firebase", json={"id_token": token}, follow_redirects=False
    )
    assert resp.status_code == 303

    session = _decode_session_cookie(resp.headers["set-cookie"])
    assert session["role"] == "superadmin"
    assert session["uid"] == "owner-google-uid"
    assert session["tenant_id"] == "tenant:breakglass"


def test_firebase_login_rejects_missing_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem project_id a rota devolve 503 (fail-closed)."""
    monkeypatch.setenv("KUBO_FIREBASE_PROJECT_ID", "")
    configured_client = TestClient(create_app(), base_url="https://testserver")

    resp = configured_client.post("/auth/firebase", json={"id_token": "x"}, follow_redirects=False)
    assert resp.status_code == 503


def test_firebase_login_rejects_missing_id_token(client: TestClient) -> None:
    """POST /auth/firebase sem id_token é rejeitado na borda (Pydantic 422)."""
    resp = client.post("/auth/firebase", json={}, follow_redirects=False)
    assert resp.status_code == 422


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
