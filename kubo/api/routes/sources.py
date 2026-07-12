"""Rota de Fontes (ADR-0014 UI, paridade `FontesScreen.jsx`): lista das fontes com
os fatos da coleta — kind, última coleta, itens acumulados, badge de recência (E4).

Rota SÍNCRONA (store bloqueante; uma conexão por request). SEM detalhe de fonte
(E1) e SEM "Adicionar fonte" (E1: backend inexistente) — desvios declarados. O badge
mostra o FATO ('última coleta há Nd'), não julga saúde (E4). Escala pessoal: poucas
fontes, sem paginação nem busca (busca é toggle/luxo de M6)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

from kubo.api.rendering import templates
from kubo.store import client, knowledge
from kubo.store.knowledge import SourceStat


def _sort_key(s: SourceStat) -> tuple[int, str]:
    """Chave de ordenação (usada com reverse=True): fontes que já coletaram no topo
    (por último carimbo, mais recente primeiro), as sem coleta por último — a recência
    é o eixo desta tela. Flag 1 = coletou, 0 = nunca; carimbo ISO desempata."""
    return (1 if s.last_collected_at else 0, s.last_collected_at or "")


router = APIRouter()


@router.get("")
def list_page(request: Request) -> Response:
    """Lista as fontes com contagem de itens e recência da coleta (E4)."""
    with client.connect() as db:
        sources = knowledge.sources_with_stats(db)
    sources = sorted(sources, key=_sort_key, reverse=True)
    return templates.TemplateResponse(request, "sources/list.html", {"sources": sources})
