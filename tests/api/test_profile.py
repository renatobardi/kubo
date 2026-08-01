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

from kubo.errors import ConfigError, ExecutorError, MalformedOutputError
from kubo.store import tenancy as _tenancy

_CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')

_PROFILE_USER = SimpleNamespace(
    id=RecordID("user", "breakglass-owner"),
    firebase_uid="user:breakglass-owner",
    email="renato@example.com",
)

_PROFILE = SimpleNamespace(
    display_name="Renato",
    language="pt-BR",
    timezone="America/Sao_Paulo",
    work_context="Arquiteto de plataforma.",
)

_MEMBERSHIP = SimpleNamespace(
    tenant=RecordID("tenant", "breakglass"),
    role="owner",
    theme="system",
)


def _update_profile(
    db: object,
    *,
    user_id: object,
    display_name: str,
    language: str,
    timezone: str,
    work_context: str | None = None,
) -> SimpleNamespace:
    text = display_name.strip()
    if not text or len(text) > 64:
        raise _tenancy.StoreError("display_name must be 1-64 characters")
    if work_context is not None and len(work_context.strip()) > _tenancy.MAX_WORK_CONTEXT_LENGTH:
        raise _tenancy.StoreError("work_context must be at most 4000 characters")
    _PROFILE.display_name = display_name
    _PROFILE.language = language
    _PROFILE.timezone = timezone
    if work_context is not None:
        _PROFILE.work_context = work_context.strip() or None
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
    """A tela de perfil mostra nome, idioma, timezone, contexto de trabalho, tema e avatar."""
    resp = authed_client.get("/profile")

    assert resp.status_code == 200
    assert "Renato" in resp.text
    assert "pt-BR" in resp.text
    assert "America/Sao_Paulo" in resp.text
    assert "Arquiteto de plataforma." in resp.text
    assert 'name="work_context"' in resp.text
    assert 'value="system"' in resp.text
    assert "gravatar.com" in resp.text


def test_post_profile_updates_and_redirects(authed_client: TestClient) -> None:
    """POST /profile salva nome, idioma, timezone e contexto de trabalho."""
    resp = authed_client.post(
        "/profile",
        data={
            "csrf": _csrf(authed_client),
            "display_name": "Bardi",
            "language": "en-US",
            "timezone": "UTC",
            "work_context": "Engenheiro de dados.",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/profile"
    assert _PROFILE.work_context == "Engenheiro de dados."


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
    )
    monkeypatch.setattr("kubo.store.tenancy.get_user", lambda db, user_id: no_email_user)

    import hashlib

    expected_digest = hashlib.sha256(b"").hexdigest()
    resp = authed_client.get("/profile")

    assert resp.status_code == 200
    assert expected_digest in resp.text
    assert "gravatar.com" in resp.text


def test_sidebar_footer_links_to_profile(authed_client: TestClient) -> None:
    """O footer da sidebar tem um link para /profile."""
    resp = authed_client.get("/distilled")
    assert resp.status_code == 200
    assert 'href="/profile"' in resp.text


def test_sidebar_footer_shows_gravatar_avatar(authed_client: TestClient) -> None:
    """O footer da sidebar mostra o avatar Gravatar (não a letra hardcoded)."""
    resp = authed_client.get("/distilled")
    assert resp.status_code == 200
    assert "gravatar.com" in resp.text
    assert ">R<" not in resp.text  # o "R" hardcoded foi removido


def test_sidebar_footer_shows_display_name_after_profile_update(authed_client: TestClient) -> None:
    """Após atualizar o perfil, o nome aparece no footer da sidebar em outras páginas."""
    authed_client.post(
        "/profile",
        data={
            "csrf": _csrf(authed_client),
            "display_name": "Bardi Test",
            "language": "en-US",
            "timezone": "UTC",
        },
        follow_redirects=False,
    )
    resp = authed_client.get("/distilled")
    assert resp.status_code == 200
    assert "Bardi Test" in resp.text


def test_profile_page_has_logout_button(authed_client: TestClient) -> None:
    """A página de perfil tem um botão de logout (POST /logout)."""
    resp = authed_client.get("/profile")
    assert resp.status_code == 200
    assert 'action="/logout"' in resp.text
    assert "Sair" in resp.text


def test_profile_page_has_gravatar_link(authed_client: TestClient) -> None:
    """A página de perfil explica que o avatar vem do Gravatar com link."""
    resp = authed_client.get("/profile")
    assert resp.status_code == 200
    assert "gravatar.com" in resp.text
    assert "Gravatar" in resp.text


def test_update_theme_caches_in_session(authed_client: TestClient) -> None:
    """Salvar tema atualiza a sessão e reflete na próxima renderização."""
    authed_client.post(
        "/membership/preferences",
        data={
            "csrf": _csrf(authed_client),
            "theme": "dark",
        },
        follow_redirects=False,
    )
    # Após salvar dark, a página de perfil deve mostrar dark selecionado
    resp = authed_client.get("/profile")
    assert resp.status_code == 200
    assert 'value="dark" selected' in resp.text


def test_post_profile_rejects_oversized_work_context(authed_client: TestClient) -> None:
    """Contexto de trabalho acima de 4000 caracteres volta 400 e não persiste."""
    resp = authed_client.post(
        "/profile",
        data={
            "csrf": _csrf(authed_client),
            "display_name": "Renato",
            "language": "pt-BR",
            "timezone": "America/Sao_Paulo",
            "work_context": "x" * (_tenancy.MAX_WORK_CONTEXT_LENGTH + 1),
        },
    )

    assert resp.status_code == 400


class _FakeReviewer:
    """Fake do reviser de contexto de trabalho."""

    def __init__(self, result: str | Exception) -> None:
        self._result = result
        self.calls: list[str] = []

    def review(self, draft: str) -> str:
        self.calls.append(draft)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _set_reviewer(monkeypatch: pytest.MonkeyPatch, result: str | Exception) -> _FakeReviewer:
    fake = _FakeReviewer(result)
    monkeypatch.setattr("kubo.api.routes.profile._get_reviewer", lambda: fake)
    return fake


def test_review_work_context_returns_json(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /profile/work-context/review retorna JSON com o texto revisado."""
    fake = _set_reviewer(monkeypatch, "Arquiteto de dados em escala.")

    resp = authed_client.post(
        "/profile/work-context/review",
        data={
            "csrf": _csrf(authed_client),
            "work_context": "  arquiteto dados  ",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"work_context": "Arquiteto de dados em escala."}
    assert fake.calls == ["  arquiteto dados  "]


def test_review_work_context_requires_csrf(authed_client: TestClient) -> None:
    """POST /profile/work-context/review sem CSRF é recusado."""
    resp = authed_client.post(
        "/profile/work-context/review",
        data={"work_context": "x"},
    )

    assert resp.status_code == 403


def test_review_work_context_rejects_oversized_input(authed_client: TestClient) -> None:
    """Texto acima de 4000 caracteres é recusado antes de chamar o LLM."""
    resp = authed_client.post(
        "/profile/work-context/review",
        data={
            "csrf": _csrf(authed_client),
            "work_context": "x" * (_tenancy.MAX_WORK_CONTEXT_LENGTH + 1),
        },
    )

    assert resp.status_code == 400


@pytest.mark.parametrize(
    "exc",
    [
        ExecutorError("boom"),
        MalformedOutputError("bad json"),
        ConfigError("work_context_reviewer persona not found"),
    ],
)
def test_review_work_context_returns_503_on_executor_failure(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """Falhas do executor/config viram 503 com corpo JSON `{"error": ...}` (ADR-0046 §IV)."""
    _set_reviewer(monkeypatch, exc)

    resp = authed_client.post(
        "/profile/work-context/review",
        data={
            "csrf": _csrf(authed_client),
            "work_context": "arquiteto dados",
        },
    )

    assert resp.status_code == 503
    assert resp.json()["error"] == "Escrita indisponível por erro de configuração."
