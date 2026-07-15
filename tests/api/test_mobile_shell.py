"""Testes do shell mobile (0019 marco 19.2): bottom tab bar, sidebar escondida em mobile,
h-dvh, safe-area, CSS do nav-collapsed escopado a `md`. Estrutura, não pixel — mesma
convenção de tests/api/test_shell.py."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kubo.api.nav import NAV
from kubo.api.rendering import _GROUP_TO_MOBILE_TAB, _current_mobile_tab_key, _current_nav_item


def test_every_nav_group_maps_to_a_mobile_tab() -> None:
    """Blindagem contra desalinhamento futuro: um grupo novo em NAV sem entrada
    correspondente em _GROUP_TO_MOBILE_TAB cairia silenciosamente em 'mais'."""
    groups = {item["group"] for item in NAV if item["group"] is not None}
    assert groups <= _GROUP_TO_MOBILE_TAB.keys()


def test_current_mobile_tab_key_maps_nav_groups_to_tabs() -> None:
    """Painel (grupo None, rota '/') → painel; Conhecimento → saber; Trabalho → trabalho;
    Distribuição → distribuicao; path sem item de nav (ex.: '/more') → mais (fallback)."""
    assert _current_mobile_tab_key(_current_nav_item("/")) == "painel"
    assert _current_mobile_tab_key(_current_nav_item("/distilled")) == "saber"
    assert _current_mobile_tab_key(_current_nav_item("/entities/abc123")) == "saber"
    assert _current_mobile_tab_key(_current_nav_item("/sources")) == "saber"
    assert _current_mobile_tab_key(_current_nav_item("/flows")) == "trabalho"
    assert _current_mobile_tab_key(_current_nav_item("/runs")) == "trabalho"
    assert _current_mobile_tab_key(_current_nav_item("/destinations")) == "distribuicao"
    assert _current_mobile_tab_key(_current_nav_item("/dispatches")) == "distribuicao"
    assert _current_mobile_tab_key(_current_nav_item("/more")) == "mais"
    assert _current_mobile_tab_key(_current_nav_item("/nope")) == "mais"


def test_tab_bar_renders_five_tabs_with_routes(authed_client: TestClient) -> None:
    """A tab bar tem os 5 destinos do plano 0019 (Painel · Saber · Trabalho · Distribuição
    · Mais), cada um linkando pra rota correta."""
    html = authed_client.get("/").text
    for label, href in (
        ("Painel", 'href="/"'),
        ("Saber", 'href="/distilled"'),
        ("Trabalho", 'href="/flows"'),
        ("Distribuição", 'href="/destinations"'),
        ("Mais", 'href="/more"'),
    ):
        assert label in html
        assert href in html


def test_tab_bar_hidden_on_desktop_sidebar_hidden_on_mobile(authed_client: TestClient) -> None:
    """A tab bar só existe em mobile (`md:hidden`); a sidebar só existe em desktop
    (`hidden md:flex`) — desktop nunca muda, mobile é aditivo (risco 5 do advisor)."""
    html = authed_client.get("/").text
    assert 'aria-label="Navegação principal"' in html
    tab_bar_start = html.find('aria-label="Navegação principal"')
    tab_bar_tag_start = html.rfind("<nav", 0, tab_bar_start)
    tab_bar_tag = html[tab_bar_tag_start : html.find(">", tab_bar_tag_start)]
    assert "md:hidden" in tab_bar_tag
    aside_start = html.find("<aside")
    aside_tag = html[aside_start : html.find(">", aside_start)]
    assert "hidden" in aside_tag and "md:flex" in aside_tag


def test_tab_bar_active_tab_has_aria_current(authed_client: TestClient) -> None:
    """A aba correspondente à tela atual leva aria-current='page'; as outras não.
    Escopado ao bloco da tab bar — a sidebar tem os mesmos hrefs com seu próprio
    marcador de ativo (bg-sidebar-accent), não aria-current."""
    html = authed_client.get("/distilled").text
    tab_bar_start = html.find('aria-label="Navegação principal"')
    tab_bar = html[html.rfind("<nav", 0, tab_bar_start) : html.find("</nav>", tab_bar_start)]
    saber_start = tab_bar.find('href="/distilled"')
    saber_tag = tab_bar[tab_bar.rfind("<a", 0, saber_start) : tab_bar.find(">", saber_start)]
    assert 'aria-current="page"' in saber_tag
    painel_start = tab_bar.find('href="/"')
    painel_tag = tab_bar[painel_start : tab_bar.find(">", painel_start)]
    assert 'aria-current="page"' not in painel_tag


def test_tab_bar_has_safe_area_padding(authed_client: TestClient) -> None:
    """Risco 2 (advisor): safe-area inset via env() no padding da tab bar."""
    html = authed_client.get("/").text
    assert "env(safe-area-inset-bottom)" in html


def test_viewport_meta_has_viewport_fit_cover(authed_client: TestClient) -> None:
    """Risco 2 (advisor): viewport-fit=cover é pré-condição pro env(safe-area-*) funcionar."""
    html = authed_client.get("/").text
    assert "viewport-fit=cover" in html


def test_shell_uses_dvh_not_screen_height(authed_client: TestClient) -> None:
    """Risco 1 (advisor): h-screen quebra no Safari iOS (toolbar dinâmica) — usar h-dvh."""
    html = authed_client.get("/").text
    assert "h-dvh" in html
    assert "h-screen" not in html


def test_nav_collapsed_css_scoped_to_md(authed_client: TestClient) -> None:
    """Risco 3 (advisor): o CSS inline de `nav-collapsed` não pode vazar pra mobile —
    escopado a `@media (min-width: 768px)` (breakpoint md do Tailwind)."""
    html = authed_client.get("/").text
    style_start = html.find("<style>")
    style_end = html.find("</style>")
    style_block = html[style_start:style_end]
    collapsed_pos = style_block.find("html.nav-collapsed aside")
    media_pos = style_block.rfind("@media (min-width: 768px)", 0, collapsed_pos)
    assert media_pos != -1, "regra nav-collapsed sem @media(min-width:768px) antes dela"


def test_more_route_renders_secondary_links(authed_client: TestClient) -> None:
    """A tab 'Mais' leva a uma página simples com links do resto da nav não coberto
    diretamente pelas outras 4 tabs (Entidades, Fontes, Execuções, Envios)."""
    html = authed_client.get("/more").text
    for href in ('href="/entities"', 'href="/sources"', 'href="/runs"', 'href="/dispatches"'):
        assert href in html


def test_more_mobile_header_shows_mais_not_generic_kubo(authed_client: TestClient) -> None:
    """/more não tem item de NAV — sem mobile_title, o header mobile cairia no
    fallback genérico 'Kubo' em vez de 'Mais' (achado do smoke visual 19.6)."""
    html = authed_client.get("/more").text
    header_start = html.find("text-[1.875rem]")
    header = html[html.rfind("<header", 0, header_start) : html.find("</header>", header_start)]
    assert ">Mais<" in header


def test_more_requires_auth(client: TestClient) -> None:
    assert client.get("/more", follow_redirects=False).status_code == 303


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
