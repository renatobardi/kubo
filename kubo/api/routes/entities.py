"""Rotas de Entidades (ADR-0014 UI, paridade tab `ConhecimentoScreen.jsx`, versão E2):
lista tipada (glifo por kind + nome + badge de tipo + contagem de menções) e detalhe
com os destilados que mencionam a entidade.

Rota SÍNCRONA (store bloqueante; uma conexão por request). SEM sparkline e SEM
relações (E2: corte por dado inexistente — `relates_to` sem produtor, `mentions` sem
timestamp). Nome de entidade é conteúdo derivado de LLM (hostil): renderizado escapado.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.responses import Response
from surrealdb import RecordID

from kubo.api.pagination import clamp_size, clamp_start
from kubo.api.rendering import templates
from kubo.api.session import SessionContext, resolve_session
from kubo.store import client, knowledge

router = APIRouter()

_ENTITY_TABLE = "entity"


@router.get("")
def list_page(
    request: Request,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 50,
    q: Annotated[str, Query()] = "",
) -> Response:
    """Lista de entidades, mais mencionadas primeiro (E2), com busca por nome/kind e
    paginação completa (0011). `size`/`start` clampados; `q` filtra na store.

    Filtra pelo tenant ativo da sessão (KUBO-130): superadmin lê qualquer tenant,
    owner/member só o seu."""
    size = clamp_size(size)
    start = clamp_start(start)
    query = q.strip()
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse("Acesso negado.", status_code=403)
        is_superadmin = ctx.role == "superadmin"
        entities = knowledge.list_entities(
            db,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            superadmin=is_superadmin,
            limit=size,
            start=start,
            query=query,
        )
        total = knowledge.count_entities(
            db,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            superadmin=is_superadmin,
            query=query,
        )
    return templates.TemplateResponse(
        request,
        "entities/list.html",
        {"entities": entities, "start": start, "size": size, "total": total, "query": query},
    )


@router.get("/{entity_id}")
def detail(request: Request, entity_id: str) -> Response:
    """Detalhe de uma entidade: tipo + contagem de menções + os destilados que a
    mencionam (cards com título/fonte/data). Id inexistente vira 404; entidade de
    outro tenant sem superadmin vira 403 (KUBO-130).

    A tabela do RecordID é SEMPRE `entity` — o path param só escolhe a chave, nunca
    a tabela; um id inexistente vira 404, não porta para ler outro registro."""
    key = entity_id.strip()
    if not key:
        return templates.TemplateResponse(
            request, "entities/not_found.html", {"raw": entity_id}, status_code=404
        )
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse("Acesso negado.", status_code=403)
        rid = RecordID(_ENTITY_TABLE, key)
        # `_entity_in_tenant` faz o gate tri-state: None=404 (não existe),
        # False=403 (outro tenant), True=ok (próprio tenant ou superadmin).
        guard = _entity_in_tenant(db, rid, ctx)
        if guard is None:
            view = None
        elif not guard:
            return PlainTextResponse("Acesso negado.", status_code=403)
        else:
            view = knowledge.read_entity(db, rid, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    if view is None:
        return templates.TemplateResponse(
            request, "entities/not_found.html", {"raw": entity_id}, status_code=404
        )
    return templates.TemplateResponse(request, "entities/detail.html", {"view": view})


def _entity_in_tenant(db: Any, entity_id: RecordID, ctx: SessionContext) -> bool | None:
    """Tri-state: True se a entidade pertence ao tenant ativo (ou superadmin),
    False se pertence a outro tenant, None se não existe. O caller distingue 404
    de 403 (KUBO-130)."""
    if ctx.role == "superadmin":
        return True
    rows = db.query("SELECT tenant_id FROM $e;", {"e": entity_id})
    if not rows:
        return None
    return rows[0].get("tenant_id") == ctx.tenant_id
