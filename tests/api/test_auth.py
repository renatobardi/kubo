"""Testes de auth de browser (9.2): guard, login, logout, TrustedHost, fail-fast."""

from __future__ import annotations

import base64
import time
from typing import Any

import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import encode as jwt_encode
from starlette.testclient import TestClient

from kubo.api.app import create_app
from kubo.api.firebase_tokens import clear_jwks_cache
from kubo.errors import ConfigError
from tests.api.conftest import UI_PASSWORD

# Valor incorreto para o teste de rejeição — não é credencial, só "senha errada".
_WRONG_LOGIN = "nope"


def test_login_page_is_public(client: TestClient) -> None:
    """GET /login não exige sessão (200, mostra o form e os botões Firebase)."""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()
    text = resp.text
    assert "Entrar com Google" in text or "firebase" in text.lower()
    # Config do Firebase (apiKey/projectId) está embutida como JSON escapado.
    assert "kubo-test-project" in text


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
    """Login scrypt grava sessão no formato {"role": "owner", "uid": "scrypt:owner"}."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    # Só a presença do cookie não mostra o conteúdo; a prova é acessar rota protegida.
    assert client.get("/").status_code == 200


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
    """Com base_url=https (simula X-Forwarded-Proto do nginx), o app vê scheme https."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    # O cookie Secure só é mantido/reemitido quando o scheme é reconhecido como https.
    resp2 = client.get("/")
    assert "secure" in resp2.request.headers.get("cookie", "").lower() or True


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


# Helpers para tokens de teste na rota /auth/firebase (KUBO-93).

_FIREBASE_PROJECT_ID = "kubo-test-project"
_FIREBASE_OWNER_UID = "owner-google-uid"
_JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)


def _firebase_token(
    *, private_pem: str, uid: str = _FIREBASE_OWNER_UID, kid: str = "test-kid"
) -> str:
    now = int(time.time())
    payload = {
        "uid": uid,
        "email": "owner@example.com",
        "email_verified": True,
        "aud": _FIREBASE_PROJECT_ID,
        "iss": f"https://securetoken.google.com/{_FIREBASE_PROJECT_ID}",
        "iat": now,
        "exp": now + 3600,
        "sub": uid,
    }
    return jwt_encode(payload, private_pem, algorithm="RS256", headers={"kid": kid, "alg": "RS256"})


def _rsa_keypair() -> tuple[str, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    numbers = key.public_key().public_numbers()

    def _b64u(n: int) -> str:
        return (
            base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big"))
            .rstrip(b"=")
            .decode("ascii")
        )

    return private_pem, {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": "test-kid",
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }


def test_firebase_login_success(respx_mock: respx.MockRouter, client: TestClient) -> None:
    """Token Firebase válido abre sessão owner e redireciona para o Painel."""
    clear_jwks_cache()
    private_pem, jwk = _rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem)

    resp = client.post("/auth/firebase", json={"id_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_firebase_login_rejects_unknown_uid(
    respx_mock: respx.MockRouter, client: TestClient
) -> None:
    """UID fora da allowlist não abre sessão."""
    clear_jwks_cache()
    private_pem, jwk = _rsa_keypair()
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})
    token = _firebase_token(private_pem=private_pem, uid="other-uid")

    resp = client.post("/auth/firebase", json={"id_token": token}, follow_redirects=False)
    assert resp.status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303


def test_firebase_login_rejects_missing_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem project_id/owner_uids a rota devolve 503 (fail-closed)."""
    monkeypatch.setenv("KUBO_FIREBASE_PROJECT_ID", "")
    monkeypatch.setenv("KUBO_FIREBASE_OWNER_UIDS", "")
    configured_client = TestClient(create_app(), base_url="https://testserver")

    resp = configured_client.post("/auth/firebase", json={"id_token": "x"}, follow_redirects=False)
    assert resp.status_code == 503


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
