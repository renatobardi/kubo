"""Rotas de Destilados (ADR-0013 leituras, ADR-0014 UI): lista paginada, busca
semântica (partial HTMX) e detalhe com cadeia de proveniência.

Summaries são conteúdo derivado de LLM sobre dado HOSTIL — renderizados como texto
plano escapado pelo autoescape do Jinja (E1: nada de markdown→HTML, `|safe` proibido).
Rotas SÍNCRONAS (ADR-0014): a store é bloqueante; uma conexão por request (o SDK
não é compartilhável entre as threads do pool). Busca degrada com alerta *tinted*
quando o embedder falta/erra (E-f), mantendo o browse navegável.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Query, Request
from starlette.responses import Response
from surrealdb import RecordID

from kubo.api.rendering import templates
from kubo.embedding import GeminiEmbedder
from kubo.errors import ConfigError, EmbeddingError
from kubo.store import client, knowledge
from kubo.store.knowledge import SearchHit

_log = structlog.get_logger(__name__)
router = APIRouter()

_PAGE_SIZE = 20
_SEARCH_K = 20
_UI_EMBED_TIMEOUT = 10.0  # UI degrada rápido; não os 60s do backfill (E-f)
_DISTILLED_TABLE = "distilled"


def _dedupe_by_distilled(hits: list[SearchHit]) -> list[SearchHit]:
    """Colapsa hits por chunk a um por distilled, mantendo o de menor distância.

    `search` devolve um hit por chunk; dois chunks do mesmo distilled duplicariam o
    resultado. (RecordID é unhashable no SDK 2.0.0 — a chave é a forma string do id.)"""
    best: dict[str, SearchHit] = {}
    for hit in hits:
        key = str(hit.distilled)
        current = best.get(key)
        if current is None or hit.score < current.score:
            best[key] = hit
    return sorted(best.values(), key=lambda h: h.score)


@router.get("")
def list_page(request: Request, start: int = Query(0)) -> Response:
    """Página do acervo, mais recentes primeiro. prev/next sem total (o total é luxo
    cortável): pede uma linha a mais para saber se há próxima sem uma contagem."""
    start = max(0, start)
    with client.connect() as db:
        rows = knowledge.list_distilled(db, limit=_PAGE_SIZE + 1, start=start)
    has_next = len(rows) > _PAGE_SIZE
    items = rows[:_PAGE_SIZE]
    return templates.TemplateResponse(
        request,
        "distilled/list.html",
        {
            "items": items,
            "has_prev": start > 0,
            "has_next": has_next,
            "prev_start": max(0, start - _PAGE_SIZE),
            "next_start": start + _PAGE_SIZE,
        },
    )


@router.get("/search")
def search(request: Request, q: str = Query("")) -> Response:
    """Busca semântica (partial HTMX, k=20, sem paginação — E6). Query vazia = partial
    vazio. Falha/ausência do embedder = alerta *tinted*, browse segue navegável (E-f)."""
    query = q.strip()
    if not query:
        return templates.TemplateResponse(
            request, "distilled/_results.html", {"results": [], "query": "", "error": None}
        )
    try:
        embedder = GeminiEmbedder.from_env(timeout=_UI_EMBED_TIMEOUT)
        vector = embedder.embed([query])[0]
    except (ConfigError, EmbeddingError) as exc:
        # Sem retry (E-f): free-tier é finito e o dono relança a busca se quiser.
        _log.warning("api.search.unavailable", error=type(exc).__name__)
        return templates.TemplateResponse(
            request,
            "distilled/_results.html",
            {"results": [], "query": query, "error": "Busca indisponível no momento."},
        )
    with client.connect() as db:
        hits = _dedupe_by_distilled(knowledge.search(db, embedding=vector, k=_SEARCH_K))
        results = [
            view
            for hit in hits
            if (view := knowledge.read_distilled(db, hit.distilled)) is not None
        ]
    return templates.TemplateResponse(
        request, "distilled/_results.html", {"results": results, "query": query, "error": None}
    )


@router.get("/{distilled_id}")
def detail(request: Request, distilled_id: str) -> Response:
    """Detalhe de um destilado: summary (texto plano escapado) + claims + cadeia de
    proveniência (distilled → item → source, e os runs que o produziram).

    A tabela do RecordID é SEMPRE `distilled` — o path param só escolhe a chave, nunca
    a tabela; um id inexistente vira 404, não uma porta para ler outro registro."""
    key = distilled_id.strip()
    view = None
    if key:
        with client.connect() as db:
            view = knowledge.read_distilled(db, RecordID(_DISTILLED_TABLE, key))
    if view is None:
        return templates.TemplateResponse(
            request, "distilled/not_found.html", {"raw": distilled_id}, status_code=404
        )
    return templates.TemplateResponse(request, "distilled/detail.html", {"view": view})
