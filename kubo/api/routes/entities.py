"""Rotas de Entidades (ADR-0014 UI, paridade tab `ConhecimentoScreen.jsx`, versão E2):
lista tipada (glifo por kind + nome + badge de tipo + contagem de menções) e detalhe
com os destilados que mencionam a entidade.

Rota SÍNCRONA (store bloqueante; uma conexão por request). SEM sparkline e SEM
relações (E2: corte por dado inexistente — `relates_to` sem produtor, `mentions` sem
timestamp). Nome de entidade é conteúdo derivado de LLM (hostil): renderizado escapado.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from starlette.responses import Response
from surrealdb import RecordID

from kubo.api.rendering import templates
from kubo.store import client, knowledge

router = APIRouter()

_PAGE_SIZE = 40  # entidades são leves (nome+kind+contagem); página folgada, escala pessoal
_ENTITY_TABLE = "entity"


@router.get("")
def list_page(request: Request, start: Annotated[int, Query()] = 0) -> Response:
    """Lista de entidades, mais mencionadas primeiro (E2). Paginação peek+1."""
    start = max(0, start)
    with client.connect() as db:
        rows = knowledge.list_entities(db, limit=_PAGE_SIZE + 1, start=start)
    has_next = len(rows) > _PAGE_SIZE
    return templates.TemplateResponse(
        request,
        "entities/list.html",
        {
            "entities": rows[:_PAGE_SIZE],
            "has_prev": start > 0,
            "has_next": has_next,
            "prev_start": max(0, start - _PAGE_SIZE),
            "next_start": start + _PAGE_SIZE,
        },
    )


@router.get("/{entity_id}")
def detail(request: Request, entity_id: str) -> Response:
    """Detalhe de uma entidade: tipo + contagem de menções + os destilados que a
    mencionam (cards com título/fonte/data). Id inexistente vira 404.

    A tabela do RecordID é SEMPRE `entity` — o path param só escolhe a chave, nunca a
    tabela; um id inexistente vira 404, não porta para ler outro registro."""
    key = entity_id.strip()
    view = None
    if key:
        with client.connect() as db:
            view = knowledge.read_entity(db, RecordID(_ENTITY_TABLE, key))
    if view is None:
        return templates.TemplateResponse(
            request, "entities/not_found.html", {"raw": entity_id}, status_code=404
        )
    return templates.TemplateResponse(request, "entities/detail.html", {"view": view})
