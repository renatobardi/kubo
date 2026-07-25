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
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Body, Form, Query, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from kubo.api.auth import verify_password
from kubo.api.firebase_tokens import verify_id_token
from kubo.api.rendering import templates
from kubo.errors import FirebaseTokenError

_log = structlog.get_logger(__name__)
router = APIRouter()

_FAIL_DELAY_SECONDS = 1
_LOGIN_TEMPLATE = "login.html"

# Identificador sintético para sessões abertas pelo login scrypt (break-glass).
_SCRYPT_OWNER_UID = "scrypt:owner"


def _open_session(request: Request, *, uid: str) -> None:
    """Regenera a sessão (fixation) e grava role/owner + uid + timestamp de auth."""
    request.session.clear()
    request.session["role"] = "owner"
    request.session["uid"] = uid
    request.session["auth_at"] = int(time.time())


def _safe_next(raw: str, default: str = "/") -> str:
    """Só aceita paths relativos locais como destino pós-login."""
    if not raw:
        return default
    path = urlparse(raw).path
    if not path.startswith("/") or path.startswith("//"):
        return default
    return path


def _login_context(
    request: Request, error: str | None = None, next_path: str = "/"
) -> dict[str, Any]:
    """Contexto da tela de login: mensagem de erro + config Firebase + next."""
    cfg = request.app.state.firebase_config
    return {
        "error": error,
        "next": _safe_next(next_path),
        "firebase": {
            "api_key": cfg.api_key,
            "auth_domain": cfg.auth_domain,
            "project_id": cfg.project_id,
        },
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
        return RedirectResponse(_safe_next(next), status_code=303)
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
    next_path = _safe_next(next)
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
            _open_session(request, uid=_SCRYPT_OWNER_UID)
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
    """True se project id e owner uids estão configurados (fail-closed)."""
    cfg = request.app.state.firebase_config
    return bool(cfg.project_id and request.app.state.firebase_owner_uids)


@router.post("/auth/firebase")
def firebase_login(
    request: Request,
    body: Annotated[dict[str, Any], Body()],
    next: Annotated[str, Query()] = "",
) -> Response:
    """Verifica ID token Firebase (server-side) e abre sessão owner se autorizado.

    Fail-closed: config ausente → 503; token inválido/uid não permitido → 401.
    """
    next_path = _safe_next(next)
    if not _firebase_config_ok(request):
        _log.warning("api.firebase.config_missing")
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Login Firebase indisponível.", next_path=next_path),
            status_code=503,
        )

    id_token = body.get("id_token", "")
    if not id_token:
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Token não informado.", next_path=next_path),
            status_code=400,
        )

    try:
        user = verify_id_token(
            id_token,
            request.app.state.firebase_config.project_id,
            request.app.state.firebase_owner_uids,
        )
    except FirebaseTokenError as exc:
        _log.warning("api.firebase.failed", code=exc.code)
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Autenticação Firebase falhou.", next_path=next_path),
            status_code=401,
        )

    _open_session(request, uid=user["uid"])
    return RedirectResponse(next_path, status_code=303)
