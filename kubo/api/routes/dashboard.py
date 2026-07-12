"""Painel (home) — contagens do acervo + últimas execuções (ADR-0014 UI).

Rota síncrona (a store é bloqueante); uma conexão por request. As últimas runs
discriminam a falha por `error.kind` (insumo da mini-sessão pós-M6: as runs diárias
terminam error/rate_limit no free-tier).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

from kubo.api.rendering import templates
from kubo.store import client, knowledge

router = APIRouter()

_RECENT_RUNS = 10


@router.get("/")
def dashboard(request: Request) -> Response:
    """Página inicial (Painel): contagens do acervo e as últimas execuções."""
    with client.connect() as db:
        counts = knowledge.dashboard_counts(db)
        runs = knowledge.recent_runs(db, limit=_RECENT_RUNS)
    return templates.TemplateResponse(
        request, "dashboard/index.html", {"counts": counts, "runs": runs}
    )
