"""Rota de Envios (ADR-0015, paridade `EnviosScreen` do DistribuicaoScreen.jsx):
histórico do que já saiu — artefato, canal, destino, quando e status.

Rota SÍNCRONA (a store é bloqueante; uma conexão por request). Toda a tela é GET —
o tripwire de CSRF do ADR-0014 não dispara. Leitura de `dispatch` (ADR-0015): o
artefato é sempre o digest nesta fase (rotulado "Digest" na view). Falha de envio
mostra o erro estruturado expansível, mesmo padrão de Execuções. Mesma paginação
peek/total dos Destilados/Execuções.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from starlette.responses import PlainTextResponse, Response

from kubo.api.pagination import clamp_size, clamp_start
from kubo.api.rendering import templates
from kubo.api.session import resolve_session
from kubo.store import client, knowledge

router = APIRouter()


@router.get("")
def list_page(
    request: Request,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 50,
    q: Annotated[str, Query()] = "",
) -> Response:
    """Página de envios, mais recentes primeiro, com busca (canal/destino/status) e
    paginação completa. `size`/`start` clampados; `q` filtra na store.

    Filtra pelo tenant ativo da sessão (KUBO-128)."""
    size = clamp_size(size)
    start = clamp_start(start)
    query = q.strip()
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse("Acesso negado.", status_code=403)
        dispatches = knowledge.list_dispatches(
            db,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            limit=size,
            start=start,
            query=query,
        )
        total = knowledge.count_dispatches(
            db,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            query=query,
        )
    return templates.TemplateResponse(
        request,
        "dispatches/list.html",
        {
            "dispatches": dispatches,
            "start": start,
            "size": size,
            "total": total,
            "query": query,
        },
    )
