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

from kubo.api.rendering import templates
from kubo.store import client, knowledge

router = APIRouter()

_PAGE_SIZE = 20
# error.kind que representam esgotamento de quota — badge NEUTRO ("quota"), não
# vermelho: a run falhou de verdade (status='error' intacto), mas não é falha
# do worker, é o free-tier acabando (E6). Apresentação, nunca reclassificação.
_QUOTA_KINDS = frozenset({"rate_limit", "rate_limit_exhausted"})


@router.get("")
def list_page(request: Request, start: Annotated[int, Query()] = 0) -> Response:
    """Página de execuções, mais recentes primeiro. prev/next sem total (peek+1)."""
    start = max(0, start)
    with client.connect() as db:
        rows = knowledge.list_runs(db, limit=_PAGE_SIZE + 1, start=start)
    has_next = len(rows) > _PAGE_SIZE
    return templates.TemplateResponse(
        request,
        "runs/list.html",
        {
            "runs": rows[:_PAGE_SIZE],
            "quota_kinds": _QUOTA_KINDS,
            "has_prev": start > 0,
            "has_next": has_next,
            "prev_start": max(0, start - _PAGE_SIZE),
            "next_start": start + _PAGE_SIZE,
        },
    )
