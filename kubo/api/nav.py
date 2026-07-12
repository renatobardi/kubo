"""Navegação da UI — dados, não plugin (ADR-0014 §estrutura).

Uma lista de dicts. A nav renderiza SÓ o que está implementado nesta fase (D27:
zero link morto) — Painel e Destilados. Itens futuros do D13 (Entidades, Fontes,
Fluxos, Execuções, Destinos, Envios, Catálogos) entram aqui quando a tela existir.

Labels em PT-BR (apresentação); rotas em inglês (identificador) — regra de idioma
do design system. `group` agrupa na sidebar; `None` = item de topo.
"""

from __future__ import annotations

from typing import TypedDict


class NavItem(TypedDict):
    """Um item de navegação: rótulo visível (PT-BR), rota (EN), grupo da sidebar."""

    label: str
    route: str
    group: str | None


# Ordem = ordem de exibição; itens do mesmo grupo ficam CONSECUTIVOS (o header do
# grupo é renderizado na 1ª ocorrência). Só o implementado (D27: zero link morto).
NAV: list[NavItem] = [
    {"label": "Painel", "route": "/", "group": None},
    {"label": "Destilados", "route": "/distilled", "group": "Conhecimento"},
    {"label": "Execuções", "route": "/runs", "group": "Trabalho"},
]
