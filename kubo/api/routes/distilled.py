"""Rotas de Destilados (ADR-0013 leituras, ADR-0014 UI): lista paginada, busca
semântica (partial HTMX) e detalhe com cadeia de proveniência.

Summaries são conteúdo derivado de LLM sobre dado HOSTIL — renderizados como texto
plano escapado pelo autoescape do Jinja (E1: nada de markdown→HTML, `|safe` proibido).
Rotas SÍNCRONAS (ADR-0014): a store é bloqueante; uma conexão por request (o SDK
não é compartilhável entre as threads do pool). Busca degrada com alerta *tinted*
quando o embedder falta/erra (E-f), mantendo o browse navegável.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Query, Request
from starlette.responses import Response
from surrealdb import RecordID

from kubo.api.pagination import clamp_size, clamp_start
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
_RESULTS_TEMPLATE = "distilled/_results.html"
_RELATED = 6  # máximo de "Relacionados" no detalhe (escala pessoal)


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
def list_page(
    request: Request,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 50,
) -> Response:
    """Página do acervo, mais recentes primeiro, com paginação completa (0011): total
    + seletor 50/100. `size`/`start` clampados na borda."""
    size = clamp_size(size)
    start = clamp_start(start)
    with client.connect() as db:
        items = knowledge.list_distilled(db, limit=size, start=start)
        total = knowledge.count_distilled(db)
    return templates.TemplateResponse(
        request,
        "distilled/list.html",
        {"items": items, "start": start, "size": size, "total": total},
    )


@router.get("/search")
def search(request: Request, q: Annotated[str, Query()] = "") -> Response:
    """Busca semântica (partial HTMX, k=20, sem paginação — E6). Query vazia = partial
    vazio. Falha/ausência do embedder = alerta *tinted*, browse segue navegável (E-f)."""
    query = q.strip()
    if not query:
        return templates.TemplateResponse(
            request, _RESULTS_TEMPLATE, {"results": [], "query": "", "error": None}
        )
    try:
        embedder = GeminiEmbedder.from_env(timeout=_UI_EMBED_TIMEOUT)
        vector = embedder.embed([query])[0]
    except (ConfigError, EmbeddingError) as exc:
        # Sem retry (E-f): free-tier é finito e o dono relança a busca se quiser.
        _log.warning("api.search.unavailable", error=type(exc).__name__)
        return templates.TemplateResponse(
            request,
            _RESULTS_TEMPLATE,
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
        request, _RESULTS_TEMPLATE, {"results": results, "query": query, "error": None}
    )


@router.get("/{distilled_id}")
def detail(request: Request, distilled_id: str) -> Response:
    """Detalhe de um destilado: summary (texto plano escapado) + claims + cadeia de
    proveniência (distilled → item → source, e os runs que o produziram).

    A tabela do RecordID é SEMPRE `distilled` — o path param só escolhe a chave, nunca
    a tabela; um id inexistente vira 404, não uma porta para ler outro registro."""
    key = distilled_id.strip()
    view = None
    related: list[knowledge.DistilledListItem] = []
    if key:
        rid = RecordID(_DISTILLED_TABLE, key)
        with client.connect() as db:
            view = knowledge.read_distilled(db, rid)
            if view is not None:
                related = knowledge.related_distilled(db, rid, limit=_RELATED)
    if view is None:
        return templates.TemplateResponse(
            request, "distilled/not_found.html", {"raw": distilled_id}, status_code=404
        )
    return templates.TemplateResponse(
        request, "distilled/detail.html", {"view": view, "related": related}
    )
