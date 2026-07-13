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
    assert '<h1 class="text-2xl font-semibold tracking-tight">Destilados</h1>' in html


def test_topbar_search_links_to_distilled(authed_client: TestClient) -> None:
    """A busca da barra de topo é visual e leva aos Destilados (busca real da fase 1)."""
    html = authed_client.get("/").text
    assert "Buscar" in html
    assert 'href="/distilled"' in html


def test_sidebar_footer_shows_identity(authed_client: TestClient) -> None:
    """O rodapé da sidebar traz avatar + nome + e-mail (paridade do mockup)."""
    html = authed_client.get("/").text
    assert "Renato Bardi" in html
    assert "renato@kubo.studio" in html


def test_theme_toggle_present(authed_client: TestClient) -> None:
    """O toggle de tema segue acessível (agora na barra de topo)."""
    html = authed_client.get("/").text
    assert "toggleTheme()" in html


def test_nav_items_have_icons(authed_client: TestClient) -> None:
    """[S1] Cada item de nav tem um glifo. Sanidade por dois paths lucide conhecidos:
    home (Painel) e activity (Execuções)."""
    html = authed_client.get("/").text
    assert "M9 22V12h6v10" in html  # home (Painel)
    assert "M22 12h-4l-3 9L9 3l-3 9H2" in html  # activity (Execuções)


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


def test_login_logo_is_floating_sakura(client: TestClient) -> None:
    """[S3] A tela de login também usa a sakura solta, não o favicon com fundo."""
    html = client.get("/login").text
    assert "var(--sakura-ink)" in html
    assert '<img src="/static/favicon.svg"' not in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
