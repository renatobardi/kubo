"""Testes de auth de browser (9.2): guard, login, logout, TrustedHost, fail-fast."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kubo.api.app import create_app
from kubo.errors import ConfigError
from tests.api.conftest import UI_PASSWORD

# Valor incorreto para o teste de rejeição — não é credencial, só "senha errada".
_WRONG_LOGIN = "nope"


def test_login_page_is_public(client: TestClient) -> None:
    """GET /login não exige sessão (200, mostra o form)."""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()


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


def test_session_cookie_is_httponly_and_lax(client: TestClient) -> None:
    """O cookie de sessão é HttpOnly + SameSite=Lax e SEM Secure (tailnet cifra o
    transporte — ADR-0014)."""
    resp = client.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie


def test_trusted_host_rejects_unknown_host(client: TestClient) -> None:
    """Host fora da allowlist é barrado (400) — fecha DNS rebinding."""
    resp = client.get("/healthz", headers={"host": "evil.example.com"})
    assert resp.status_code == 400


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
