"""Tab 'Mais' (mobile, sessão 0019 marco 19.5): página simples de links pro resto da
nav não coberto diretamente pelas 4 outras tabs (C3 — sem tela consolidada nova)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

from kubo.api.rendering import templates

router = APIRouter()


@router.get("/more")
def more(request: Request) -> Response:
    """Lista os itens de nav secundários (Entidades, Fontes, Execuções, Envios)."""
    return templates.TemplateResponse(request, "more/index.html", {})
