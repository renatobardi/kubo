"""Rotas de autenticação (ADR-0014): /login (GET form + POST verify), /logout.

Rotas SÍNCRONAS (`def`): `verify_password` (scrypt, ~50-150ms) e o `time.sleep(1)`
do rate-limit rodariam no threadpool do Starlette, sem congelar o event loop de
1 worker. Falha de login: sleep + log estruturado (sem senha no log, óbvio) + 401.
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from kubo.api.auth import verify_password
from kubo.api.rendering import templates

_log = structlog.get_logger(__name__)
router = APIRouter()

_FAIL_DELAY_SECONDS = 1


@router.get("/login")
def login_form(request: Request) -> Response:
    """Mostra o form de login. Já autenticado? Vai direto pro Painel."""
    if request.session.get("auth"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, password: str = Form("")) -> Response:
    """Verifica a senha. Certa: abre a sessão e redireciona ao Painel. Errada:
    dorme 1s (rate-limit), loga a tentativa e devolve 401 com o form + alerta."""
    if verify_password(password, request.app.state.password_hash):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    time.sleep(_FAIL_DELAY_SECONDS)
    client = request.client.host if request.client else "unknown"
    _log.warning("api.login.failed", client=client)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Senha incorreta."}, status_code=401
    )


@router.post("/logout")
def logout(request: Request) -> Response:
    """Encerra a sessão e volta pra tela de login."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
