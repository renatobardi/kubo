"""CSRF por synchronizer token na sessão (ADR-0018 §CSRF).

O token vive na sessão assinada (itsdangerous já assina o cookie) — zero estado no
servidor, zero dependência nova. O form leva o token num hidden input; o handler de escrita
compara com `hmac.compare_digest` (tempo constante). SameSite=Lax segue a defesa primária;
este é o cinto adicional para POSTs de escrita. Falha fechada: sessão sem token ou submissão
vazia → inválido.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.requests import Request

_SESSION_KEY = "csrf"


def csrf_token(request: Request) -> str:
    """Token CSRF da sessão, criado (token_hex) na 1ª vez que uma tela com form o pede — assim
    sessões abertas ANTES desta feature também recebem um token ao visitar o board."""
    token = request.session.get(_SESSION_KEY)
    if not token:
        token = secrets.token_hex(16)
        request.session[_SESSION_KEY] = token
    return str(token)


def verify_csrf(request: Request, submitted: str) -> bool:
    """True se `submitted` bate com o token da sessão (comparação em tempo constante). Sessão
    sem token ou submissão vazia → False (falha fechada — nunca aceita por ausência)."""
    expected = request.session.get(_SESSION_KEY)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(str(expected), str(submitted))
