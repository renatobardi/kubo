"""Rotas de Destilados (marco 9.4): lista paginada, busca semântica (partial HTMX),
detalhe com cadeia de proveniência.

Vazio no scaffold — preenchido no 9.4. Summaries renderizados como texto plano
escapado (E1: nada de markdown→HTML). Rotas síncronas (ADR-0014).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
