"""Navegação da UI — dados, não plugin (ADR-0014 §estrutura).

Uma lista de dicts. A nav renderiza SÓ o que está implementado nesta fase (D27:
zero link morto) — Painel e Destilados. Itens futuros do D13 (Entidades, Fontes,
Fluxos, Execuções, Destinos, Envios, Catálogos) entram aqui quando a tela existir.

Labels em PT-BR (apresentação); rotas em inglês (identificador) — regra de idioma
do design system. `group` agrupa na sidebar; `None` = item de topo.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class NavItem(TypedDict):
    """Um item de navegação: rótulo visível (PT-BR), rota (EN), grupo da sidebar e a
    chave do glifo (resolvida pelo macro `nav_icon` — paridade com o mockup)."""

    label: str
    route: str
    group: str | None
    icon: str


# Nomes de grupo — constantes (SonarCloud S1192: literal repetido 3x+ entre NAV e
# MOBILE_TABS). Também a fonte única que o mapeamento de tabs mobile referencia.
GROUP_KNOWLEDGE = "Conhecimento"
GROUP_WORK = "Trabalho"
GROUP_DISTRIBUTION = "Distribuição"

# Ordem = ordem de exibição; itens do mesmo grupo ficam CONSECUTIVOS (o header do
# grupo é renderizado na 1ª ocorrência). Só o implementado (D27: zero link morto).
NAV: list[NavItem] = [
    {"label": "Painel", "route": "/", "group": None, "icon": "home"},
    {"label": "Destilados", "route": "/distilled", "group": GROUP_KNOWLEDGE, "icon": "book-open"},
    {"label": "Entidades", "route": "/entities", "group": GROUP_KNOWLEDGE, "icon": "network"},
    {"label": "Fontes", "route": "/sources", "group": GROUP_KNOWLEDGE, "icon": "rss"},
    {"label": "Fluxos", "route": "/flows", "group": GROUP_WORK, "icon": "workflow"},
    {"label": "Execuções", "route": "/runs", "group": GROUP_WORK, "icon": "activity"},
    {"label": "Destinos", "route": "/destinations", "group": GROUP_DISTRIBUTION, "icon": "send"},
    {"label": "Envios", "route": "/dispatches", "group": GROUP_DISTRIBUTION, "icon": "mail"},
]


MobileTabKey = Literal["dashboard", "knowledge", "work", "distribution", "more"]


class MobileTab(TypedDict):
    """Item da bottom tab bar mobile (sessão 0019). `key` (identificador, EN — regra
    de idioma) casa com o retorno de `rendering._current_mobile_tab_key`; rótulo/rota
    podem divergir do grupo da sidebar (ex.: 'Saber' é o grupo 'Conhecimento'
    rotulado diferente — C3)."""

    key: MobileTabKey
    label: str
    route: str
    icon: str


# 5 destinos fixos do plano 0019 (Painel · Saber · Trabalho · Distribuição · Mais).
# Cada tab de grupo aponta pro 1º item do grupo correspondente na NAV; "Mais" é
# página própria (resto da nav) — não existe item de NAV equivalente.
MOBILE_TABS: list[MobileTab] = [
    {"key": "dashboard", "label": "Painel", "route": "/", "icon": "home"},
    {"key": "knowledge", "label": "Saber", "route": "/distilled", "icon": "book-open"},
    {"key": "work", "label": GROUP_WORK, "route": "/flows", "icon": "workflow"},
    {"key": "distribution", "label": GROUP_DISTRIBUTION, "route": "/destinations", "icon": "send"},
    {"key": "more", "label": "Mais", "route": "/more", "icon": "ellipsis"},
]
