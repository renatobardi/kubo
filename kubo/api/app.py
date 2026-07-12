"""Fábrica da aplicação FastAPI da UI (fase 2) — factory + middlewares + rotas.

`create_app()` monta o app: estáticos, templates, rotas por domínio (um APIRouter
cada — auth, dashboard, distilled) e os middlewares de segurança do ADR-0014
(sessão assinada, guard de auth, TrustedHost). Rotas são SÍNCRONAS por decisão
(ADR-0014): a store surrealdb é bloqueante; Starlette roda `def` em threadpool,
sem congelar o event loop de 1 worker.

`/healthz` fica FORA do guard de auth (200 fixo, sem tocar o banco) — é o
healthcheck do compose, precisa responder mesmo sem sessão.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from kubo.api.routes import auth, dashboard, distilled

_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    """Constrói e devolve o app da UI. Fábrica (não singleton de módulo) para que
    os testes montem instâncias isoladas com env próprio."""
    app = FastAPI(title="Kubo", docs_url=None, redoc_url=None, openapi_url=None)

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
    app.include_router(distilled.router, prefix="/distilled")

    return app
