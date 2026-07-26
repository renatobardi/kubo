"""Fábrica da aplicação FastAPI da UI (fase 2) — factory + middlewares + rotas.

`create_app()` monta o app: estáticos, templates, rotas por domínio (um APIRouter
cada — auth, dashboard, distilled) e os middlewares de segurança do ADR-0014.
Rotas são SÍNCRONAS por decisão (ADR-0014): a store surrealdb é bloqueante;
Starlette roda `def` em threadpool, sem congelar o event loop de 1 worker.

Ordem dos middlewares (o último `add_middleware` é o mais externo):
TrustedHost (externo — rejeita Host inválido primeiro) → Session (popula a
sessão assinada) → RequireLogin (guard, interno — lê a sessão já populada).

`/healthz` e `/static` ficam FORA do guard; `/login` também (senão não há como
autenticar). `localhost` é SEMPRE permitido no TrustedHost — o healthcheck do
compose bate em `http://localhost:8000/healthz` de dentro do container.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import Response

from kubo.api.routes import (
    auth,
    dashboard,
    destinations,
    dispatches,
    distilled,
    entities,
    flows,
    more,
    runs,
    settings,
    sources,
    telegram_webhook,
)
from kubo.errors import ConfigError

_STATIC_DIR = Path(__file__).parent / "static"

# 14 dias: o dono é o único usuário; expiry curto o obrigaria a relogar sem ganho
# real (revogação de verdade = rotacionar SESSION_SECRET). ADR-0014.
_SESSION_MAX_AGE = 14 * 24 * 3600
_SESSION_COOKIE = "kubo_session"

# Sem sessão exigida: a tela de login, o handler de login Firebase, o liveness,
# os estáticos e o webhook do Telegram (a tela de login precisa carregar CSS/JS;
# o webhook carrega seu próprio secret). Tudo mais passa pelo guard.
_PUBLIC_PATHS = frozenset({"/login", "/auth/firebase", "/healthz", "/telegram/webhook"})
# Barra final proposital: só o que está SOB /static/ é público. Sem ela, uma rota
# futura chamada, digamos, /statics passaria pelo guard sem sessão.
# /invite/<token> também é público: é a landing do convite de equipe.
_PUBLIC_PREFIXES = ("/static/", "/invite/")

# Dev/CI default quando KUBO_ALLOWED_HOSTS não é setado; prod seta o IP Tailscale
# (+ MagicDNS). `testserver` é o Host do TestClient do Starlette.
_DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "testserver")

# UID do Firebase: até 128 chars, alfanumérico + underscore/hífen/ponto/dois-pontos.
# Se a allowlist não seguir esse padrão, consideramos malformada e negamos tudo.
_UID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _parse_owner_uids(raw: str) -> set[str]:
    """Parseia KUBO_FIREBASE_OWNER_UIDS; malformado ou vazio devolve set vazio."""
    uids = {uid.strip() for uid in raw.split(",") if uid.strip()}
    if not uids or not all(_UID_RE.match(uid) for uid in uids):
        return set()
    return uids


@dataclass(frozen=True)
class FirebaseConfig:
    """Configuração pública do Firebase (apiKey, authDomain, projectId)."""

    api_key: str
    auth_domain: str
    project_id: str

    def as_firebase_js_dict(self) -> dict[str, str]:
        """Serializa no formato camelCase exigido pelo Firebase JS SDK."""
        return {
            "apiKey": self.api_key,
            "authDomain": self.auth_domain,
            "projectId": self.project_id,
        }


@dataclass(frozen=True)
class UiConfig:
    """Config da UI vinda só de env (invariante 8): hash da senha, secret da
    sessão, hosts confiáveis, config Firebase, superadmin uids e break-glass."""

    password_hash: str
    session_secret: str
    allowed_hosts: list[str]
    session_fresh_max_age: int
    firebase_config: FirebaseConfig
    superadmin_uids: set[str]
    breakglass_user_id: str
    breakglass_tenant_id: str


def _ui_config() -> UiConfig:
    """Lê a config da UI do ambiente; fail-fast se um segredo obrigatório falta.

    `localhost`/`127.0.0.1` são sempre anexados aos hosts confiáveis: o healthcheck
    do compose bate em localhost de dentro do container, e sem isso um
    `KUBO_ALLOWED_HOSTS` restrito ao IP Tailscale deixaria o container unhealthy."""
    password_hash = os.environ.get("KUBO_PASSWORD_HASH")
    session_secret = os.environ.get("SESSION_SECRET")
    if not password_hash or not session_secret:
        raise ConfigError(
            "KUBO_PASSWORD_HASH e SESSION_SECRET são obrigatórios para a UI "
            "(invariante 8: segredo por referência de env). Gere o hash com "
            "`python -m kubo.api.hashpw`."
        )
    configured = [
        h.strip() for h in os.environ.get("KUBO_ALLOWED_HOSTS", "").split(",") if h.strip()
    ]
    allowed = configured or list(_DEFAULT_ALLOWED_HOSTS)
    for loopback in ("localhost", "127.0.0.1"):
        if loopback not in allowed:
            allowed.append(loopback)
    project_id = os.environ.get("KUBO_FIREBASE_PROJECT_ID", "")
    # Owner uids legados viram superadmin uids (ADR-0041 §VI).
    superadmin_uids = _parse_owner_uids(os.environ.get("KUBO_FIREBASE_OWNER_UIDS", ""))
    auth_domain = os.environ.get("KUBO_FIREBASE_AUTH_DOMAIN", "")
    if not auth_domain and project_id:
        auth_domain = f"{project_id}.firebaseapp.com"
    firebase_config = FirebaseConfig(
        api_key=os.environ.get("KUBO_FIREBASE_API_KEY", ""),
        auth_domain=auth_domain,
        project_id=project_id,
    )
    try:
        fresh_max_age = int(os.environ.get("SESSION_FRESH_MAX_AGE", "600"))
    except ValueError:
        fresh_max_age = 600
    return UiConfig(
        password_hash=password_hash,
        session_secret=session_secret,
        allowed_hosts=allowed,
        session_fresh_max_age=fresh_max_age,
        firebase_config=firebase_config,
        superadmin_uids=superadmin_uids,
        breakglass_user_id=os.environ.get("KUBO_BREAKGLASS_USER_ID", ""),
        breakglass_tenant_id=os.environ.get("KUBO_BREAKGLASS_TENANT_ID", ""),
    )


class RequireLoginMiddleware(BaseHTTPMiddleware):
    """Redireciona toda requisição sem sessão válida para /login, exceto rotas públicas.

    Guard num único ponto (não uma dependency por rota) — não há como esquecer de
    proteger uma rota nova. Sessão válida = `owner`, `member` ou `superadmin`."""

    _VALID_ROLES = {"owner", "member", "superadmin"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        public = path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES)
        if public or request.session.get("role") in self._VALID_ROLES:
            return await call_next(request)
        return RedirectResponse("/login", status_code=303)


def create_app() -> FastAPI:
    """Constrói e devolve o app da UI. Fábrica (não singleton de módulo) para que
    os testes montem instâncias isoladas com env próprio. Fail-fast se faltar segredo."""
    cfg = _ui_config()
    app = FastAPI(title="Kubo", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.password_hash = cfg.password_hash
    app.state.session_fresh_max_age = cfg.session_fresh_max_age
    app.state.firebase_config = cfg.firebase_config
    app.state.superadmin_uids = cfg.superadmin_uids
    app.state.breakglass_user_id = cfg.breakglass_user_id
    app.state.breakglass_tenant_id = cfg.breakglass_tenant_id

    # Estáticos: htmx vendorizado, font Inter self-hosted, favicon sakura e o
    # app.css gerado pelo Tailwind. O diretório existe no repo (htmx/font/favicon
    # versionados), então StaticFiles não levanta no startup mesmo sem o app.css.
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        """Liveness sem auth e sem banco — é o healthcheck do compose."""
        return "ok"

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(more.router)
    app.include_router(distilled.router, prefix="/distilled")
    app.include_router(flows.router, prefix="/flows")
    app.include_router(runs.router, prefix="/runs")
    app.include_router(sources.router, prefix="/sources")
    app.include_router(entities.router, prefix="/entities")
    app.include_router(destinations.router, prefix="/destinations")
    app.include_router(dispatches.router, prefix="/dispatches")
    app.include_router(telegram_webhook.router, prefix="/telegram")
    app.include_router(settings.router, prefix="/settings")

    # add_middleware empilha do interno para o externo: o ÚLTIMO é o mais externo.
    app.add_middleware(RequireLoginMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.session_secret,
        session_cookie=_SESSION_COOKIE,
        max_age=_SESSION_MAX_AGE,
        same_site="lax",
        https_only=True,  # ADR-0035: exposição HTTPS pública exige Secure global
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts)

    return app
