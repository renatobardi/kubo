"""Rota de Execuções (ADR-0014 UI, paridade `ExecucoesScreen.jsx`): lista paginada
de runs com o erro estruturado expansível.

Rota SÍNCRONA (a store é bloqueante; uma conexão por request). Toda a tela é GET —
o tripwire de CSRF do ADR-0014 não dispara (sessão 0010 §segurança). `error.kind`
discrimina `quota` de falha real na APRESENTAÇÃO; o `status` armazenado nunca é
reclassificado (E6). Mesma paginação peek+1 dos Destilados (total é luxo cortável).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from starlette.responses import Response

from kubo.api.pagination import clamp_size, clamp_start
from kubo.api.rendering import templates
from kubo.store import client, knowledge

router = APIRouter()

# error.kind que representam esgotamento de quota — badge NEUTRO ("quota"), não
# vermelho: a run falhou de verdade (status='error' intacto), mas não é falha
# do worker, é o free-tier acabando (E6). Apresentação, nunca reclassificação.
_QUOTA_KINDS = frozenset({"rate_limit", "rate_limit_exhausted"})


@router.get("")
def list_page(
    request: Request,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 50,
    q: Annotated[str, Query()] = "",
) -> Response:
    """Página de execuções, mais recentes primeiro, com busca (worker/status) e
    paginação completa (0011). `size`/`start` clampados; `q` filtra na store."""
    size = clamp_size(size)
    start = clamp_start(start)
    query = q.strip()
    with client.connect() as db:
        runs = knowledge.list_runs(db, limit=size, start=start, query=query)
        total = knowledge.count_runs(db, query=query)
    return templates.TemplateResponse(
        request,
        "runs/list.html",
        {
            "runs": runs,
            "quota_kinds": _QUOTA_KINDS,
            "start": start,
            "size": size,
            "total": total,
            "query": query,
        },
    )
