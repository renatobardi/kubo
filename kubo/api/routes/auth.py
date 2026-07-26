"""Rotas de autenticação (ADR-0014 + ADR-0036): /login (GET form + POST scrypt),
/auth/firebase (POST ID token) e /logout.

Rotas SÍNCRONAS (`def`): `verify_password` (scrypt, ~50-150ms) e o `time.sleep(1)`
do rate-limit rodariam no threadpool do Starlette, sem congelar o event loop de
1 worker. Falha de login: sleep + log estruturado (sem senha/token no log) + 401.
"""

from __future__ import annotations

import threading
import time
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from kubo.api.auth import verify_password
from kubo.api.firebase_tokens import verify_id_token
from kubo.api.rendering import templates
from kubo.api.urls import safe_next
from kubo.errors import FirebaseTokenError
from kubo.store import client
from kubo.store import tenancy as tenancy_store

_log = structlog.get_logger(__name__)
router = APIRouter()

_FAIL_DELAY_SECONDS = 1
_LOGIN_TEMPLATE = "login.html"

# Identificador sintético para sessões abertas pelo login scrypt (break-glass).
_SCRYPT_OWNER_UID = "scrypt:owner"


def _open_session(request: Request, *, uid: str, tenant_id: str, role: str) -> None:
    """Regenera a sessão (fixation) e grava role + uid + tenant_id + timestamp de auth."""
    request.session.clear()
    request.session["role"] = role
    request.session["uid"] = uid
    request.session["tenant_id"] = tenant_id
    request.session["auth_at"] = int(time.time())


class FirebaseLoginBody(BaseModel):
    """Corpo da requisição POST /auth/firebase (entrada externa hostil)."""

    id_token: str = Field(..., min_length=1)


def _login_context(
    request: Request, error: str | None = None, next_path: str = "/"
) -> dict[str, Any]:
    """Contexto da tela de login: mensagem de erro + config Firebase + next."""
    return {
        "error": error,
        "next": safe_next(next_path),
        "firebase": request.app.state.firebase_config.as_firebase_js_dict(),
    }


# Gate de concorrência: no máximo UMA tentativa de login processando por vez. Sem
# ele, o time.sleep(1) da falha só serializa por requisição — N conexões paralelas
# davam N chutes/segundo e prendiam N threads do pool (self-DoS das outras rotas
# síncronas). Aquisição não-bloqueante: se já há uma em voo, recusa rápido (429),
# sem gastar scrypt/sleep nem segurar thread. Rate-limit real, proporcional (ADR-0014).
_LOGIN_GATE = threading.Semaphore(1)


@router.get("/login")
def login_form(
    request: Request,
    next: Annotated[str, Query()] = "",
) -> Response:
    """Mostra o form de login. Já autenticado? Vai direto ao destino `next`."""
    if request.session.get("role") == "owner":
        return RedirectResponse(safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request, _LOGIN_TEMPLATE, _login_context(request, next_path=next)
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    """Verifica a senha. Certa: abre a sessão e redireciona ao `next` seguro. Errada:
    dorme 1s (rate-limit), loga a tentativa e devolve 401 com o form + alerta.

    Uma tentativa por vez (gate não-bloqueante): se já há login em voo, recusa na
    hora (429) sem gastar scrypt/sleep nem prender thread do pool."""
    next_path = safe_next(next)
    if not _LOGIN_GATE.acquire(blocking=False):
        client = request.client.host if request.client else "unknown"
        _log.warning("api.login.busy", client=client)
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Tente novamente em instantes.", next_path=next_path),
            status_code=429,
        )
    try:
        if verify_password(password, request.app.state.password_hash):
            _open_session(
                request,
                uid=request.app.state.breakglass_user_id or _SCRYPT_OWNER_UID,
                tenant_id=request.app.state.breakglass_tenant_id,
                role="owner",
            )
            return RedirectResponse(next_path, status_code=303)
        time.sleep(_FAIL_DELAY_SECONDS)
        client = request.client.host if request.client else "unknown"
        _log.warning("api.login.failed", client=client)
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Senha incorreta.", next_path=next_path),
            status_code=401,
        )
    finally:
        _LOGIN_GATE.release()


@router.post("/logout")
def logout(request: Request) -> Response:
    """Encerra a sessão e volta pra tela de login."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _firebase_config_ok(request: Request) -> bool:
    """True se project id está configurado (fail-closed).

    Owner uids não são mais obrigatórios para login: self-signup cria tenant
    automaticamente (ADR-0041). A allowlist passa a ser usada só para o papel
    superadmin (KUBO-116).
    """
    cfg = request.app.state.firebase_config
    return bool(cfg.project_id)


@router.post("/auth/firebase")
def firebase_login(
    request: Request,
    body: FirebaseLoginBody,
    next: Annotated[str, Query()] = "",
) -> Response:
    """Verifica ID token Firebase (server-side) e abre sessão owner se autorizado.

    Fail-closed: config ausente → 503; token inválido/uid não permitido → 401.
    """
    next_path = safe_next(next)
    if not _firebase_config_ok(request):
        _log.warning("api.firebase.config_missing")
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Login Firebase indisponível.", next_path=next_path),
            status_code=503,
        )

    try:
        token_user = verify_id_token(
            body.id_token,
            request.app.state.firebase_config.project_id,
        )
    except FirebaseTokenError as exc:
        _log.warning("api.firebase.failed", code=exc.code)
        if exc.code == "jwks_unavailable":
            return templates.TemplateResponse(
                request,
                _LOGIN_TEMPLATE,
                _login_context(request, "Login Firebase indisponível.", next_path=next_path),
                status_code=503,
            )
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Autenticação Firebase falhou.", next_path=next_path),
            status_code=401,
        )

    uid = token_user["uid"]
    if tenancy_store.is_superadmin(uid, request.app.state.superadmin_uids):
        _open_session(
            request,
            uid=uid,
            tenant_id=request.app.state.breakglass_tenant_id,
            role="superadmin",
        )
        return RedirectResponse(next_path, status_code=303)

    with client.connect() as db:
        tenant_user, tenant = tenancy_store.get_or_create_user_and_tenant(
            db,
            firebase_uid=uid,
            email=token_user.get("email") or None,
        )

    _open_session(
        request,
        uid=uid,
        tenant_id=str(tenant.id),
        role="owner",
    )
    return RedirectResponse(next_path, status_code=303)
