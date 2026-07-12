"""Painel (home) — contagens + últimas runs (marco 9.7).

Stub nesta fase do scaffold: renderiza o shell com a nav para validar base.html.
O conteúdo real (dashboard_counts + últimas runs por error.kind) entra no 9.7;
se o timebox cortar o Painel, este stub "em construção" é o que fica de pé.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from kubo.api.rendering import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Página inicial (Painel). Rota síncrona (ADR-0014): a store é bloqueante."""
    return templates.TemplateResponse(request, "dashboard/index.html")
