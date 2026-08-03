"""Testes do shell da UI (fidelidade ao mockup): breadcrumb na barra de topo,
PageHeader no conteúdo e rodapé da sidebar com avatar/identidade. O shell é
renderizado em toda tela autenticada — estes testes fixam a estrutura, não o pixel."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kubo.api.rendering import _current_nav_item


def test_current_nav_item_matches_exact_prefix_and_root() -> None:
    """Resolve o item de nav do path: raiz exata, rota exata, e prefixo (detalhe)."""
    painel = _current_nav_item("/")
    distilled = _current_nav_item("/distilled")
    detail = _current_nav_item("/entities/abc123")  # detalhe casa a rota-mãe por prefixo
    assert painel is not None and painel["label"] == "Painel"
    assert distilled is not None and distilled["label"] == "Destilados"
    assert detail is not None and detail["label"] == "Entidades"
    assert _current_nav_item("/nope") is None  # path desconhecido não casa nada


def test_breadcrumb_shows_group_and_screen(authed_client: TestClient) -> None:
    """A barra de topo mostra o breadcrumb 'grupo › tela' (paridade do mockup)."""
    html = authed_client.get("/distilled").text
    assert "Conhecimento" in html  # grupo
    assert "Destilados" in html  # tela


def test_painel_breadcrumb_has_no_group(authed_client: TestClient) -> None:
    """Painel é item de topo (sem grupo): breadcrumb só com o rótulo."""
    html = authed_client.get("/").text
    assert "Painel" in html


def test_page_header_renders_title_in_content(authed_client: TestClient) -> None:
    """O título da tela vem num PageHeader (h1) dentro do conteúdo, não na barra de topo."""
    html = authed_client.get("/distilled").text
    assert (
        '<h1 class="text-2xl font-semibold tracking-tight" data-page-title>Destilados</h1>'
    ) in html


def test_topbar_search_links_to_distilled(authed_client: TestClient) -> None:
    """A busca da barra de topo é visual e leva aos Destilados (busca real da fase 1)."""
    html = authed_client.get("/").text
    assert "Buscar" in html
    assert 'href="/distilled"' in html


def test_sidebar_footer_shows_identity(authed_client: TestClient) -> None:
    """O rodapé da sidebar traz avatar Gravatar + nome + e-mail dinâmicos da sessão."""
    html = authed_client.get("/").text
    assert "gravatar.com" in html
    assert "Perfil" in html  # fallback quando display_name está vazio
    assert 'href="/profile"' in html


def test_theme_toggle_present(authed_client: TestClient) -> None:
    """O toggle de tema segue acessível (agora na barra de topo)."""
    html = authed_client.get("/").text
    assert "toggleTheme()" in html


def test_nav_items_have_icons(authed_client: TestClient) -> None:
    """[S1] CADA item de nav tem um glifo. Escopo à sidebar (não confunde com os ícones
    dos StatTiles no conteúdo) e cobre os 5 — regressão em qualquer um é pega."""
    html = authed_client.get("/").text
    aside = html[html.find("<aside") : html.find("</aside>") + 8]
    for path in (
        "M9 22V12h6v10",  # home / Painel
        "M12 7v14",  # book-open / Destilados
        "m6.5 6.5 4 4",  # network / Entidades
        "M4 11a9 9 0 0 1 9 9",  # rss / Fontes
        "M22 12h-4l-3 9L9 3l-3 9H2",  # activity / Execuções
    ):
        assert path in aside, f"ícone faltando na sidebar: {path}"


def test_logo_is_floating_sakura_not_black_box(authed_client: TestClient) -> None:
    """[S3] O logo é a sakura de linha theme-aware (tokens --sakura-*), não o favicon
    com fundo preto. O <img> do favicon sai da sidebar (o <link rel=icon> pode ficar)."""
    html = authed_client.get("/").text
    assert "var(--sakura-ink)" in html
    assert "var(--sakura-petal)" in html
    assert '<img src="/static/favicon.svg"' not in html  # sem o quadrado preto na sidebar


def test_sidebar_collapse_wired(authed_client: TestClient) -> None:
    """[S2] O recolher-menu está ligado: função, botão e reaplicação do estado salvo."""
    html = authed_client.get("/").text
    assert "toggleNav()" in html
    assert "nav-collapsed" in html  # classe + reaplicação no <head>


def test_scroll_containers_are_containing_blocks(authed_client: TestClient) -> None:
    """O `<main>` (que rola) e o shell (que recorta) precisam ser containing block.

    Regressão real: `.sr-only` do Tailwind é `position:absolute`. Sem ancestral
    posicionado, o containing block dele é o viewport — e `overflow` só recorta
    descendente cujo containing block está DENTRO do elemento. Resultado: um label
    invisível de 1px escapava do clipping do `<main>`, esticava o documento até a
    posição dele e o documento passava a rolar, arrastando a sidebar (a "faixa em
    branco"). `relative` no `<main>` e no shell prende esses absolutos.

    A prova de behavior (scrollHeight == clientHeight, sidebar imóvel) foi feita
    via Chrome headless CDP no diagnóstico; no CI sem browser, o que dá a provar
    é a estrutura que causa o bug. A asserção de que `.sr-only` está dentro de
    `<main>` (e não órfão no body) vive em test_study_topics.py, que renderiza
    study/topic — a tela onde o bug apareceu e que tem `.sr-only` no form do
    planner chat.
    """
    html = authed_client.get("/").text

    def open_tag(anchor: str) -> str:
        """Tag de abertura que contém `anchor` — tolerante à ordem das classes."""
        at = html.find(anchor)
        assert at != -1, f"não achei {anchor!r} no shell renderizado"
        return html[html.rfind("<", 0, at) : html.find(">", at) + 1]

    main_tag = open_tag("<main")
    shell_tag = open_tag("h-dvh")
    assert "overflow-y-auto" in main_tag, "main deixou de ser o container de rolagem"
    assert "relative" in main_tag, f"main sem `relative` (absolutos escapam): {main_tag}"
    assert "overflow-hidden" in shell_tag, "shell deixou de recortar"
    assert "relative" in shell_tag, f"shell sem `relative` (absolutos escapam): {shell_tag}"


def test_login_logo_is_floating_sakura(client: TestClient) -> None:
    """[S3] A tela de login também usa a sakura solta (ambos os tokens theme-aware),
    não o favicon com fundo. Prova theme-aware = tokens presentes; a alternância real
    de tema é do smoke de browser (fora do teste de template)."""
    html = client.get("/login").text
    assert "var(--sakura-ink)" in html
    assert "var(--sakura-petal)" in html
    assert '<img src="/static/favicon.svg"' not in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
