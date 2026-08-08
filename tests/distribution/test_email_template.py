"""Tests do wrapper HTML compartilhado (email_template.wrap_email).

Verifica os marcadores estruturais da identidade do design system: glifo sakura
em tile near-black, card com ring (inset box-shadow), MSO/Outlook conditional
comments, preheader hidden, dark mode (classes kubo-*), mobile media query.
Não verifica escaping — isso é responsabilidade dos callers (digest/welcome).
"""

from __future__ import annotations

import re

from kubo.distribution.email_template import wrap_email


def test_glyph_tile_in_header() -> None:
    """Logo = glifo ❇ (&#10047;) num tile near-black com rosa."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "&#10047;" in html
    assert "#1c1917" in html  # tile near-black
    assert "#f4c9d4" in html  # glifo rosa


def test_wordmark_and_tagline() -> None:
    """Wordmark 'Kubo' e tagline aparecem no header."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert ">Kubo<" in html
    assert "The art of getting things done" in html


def test_card_uses_ring_not_border() -> None:
    """Card usa box-shadow inset (ring), não border — assinatura do design system."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "box-shadow:inset 0 0 0 1px" in html


def test_mso_conditional_comments() -> None:
    """Comentários condicionais MSO para Outlook desktop."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "[if mso]" in html
    assert "<![endif]" in html


def test_preheader_present() -> None:
    """Preheader hidden no topo (preview text em clientes de email)."""
    html = wrap_email(
        heading="Test", body_html="<p>body</p>", footer_link="https://x", preheader="Preview"
    )
    assert "display:none" in html
    assert "max-height:0" in html
    assert "Preview" in html


def test_preheader_empty_when_not_provided() -> None:
    """Sem preheader, o bloco hidden não aparece (ou aparece vazio)."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    # Sem preheader, não deve ter o bloco hidden com texto de preview
    assert "display:none" not in html or "max-height:0" not in html


def test_dark_mode_media_query() -> None:
    """@media (prefers-color-scheme: dark) no <style>."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "prefers-color-scheme: dark" in html
    assert "<style" in html


def test_dark_mode_classes() -> None:
    """Classes kubo-bg, kubo-card, kubo-fg, kubo-muted, kubo-border presentes."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "kubo-bg" in html
    assert "kubo-card" in html
    assert "kubo-fg" in html
    assert "kubo-muted" in html
    assert "kubo-border" in html


def test_divider_has_kubo_border_class() -> None:
    """O divisor do template tem class='kubo-border' para dark mode alcançá-lo."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert 'class="kubo-border"' in html


def test_mobile_media_query() -> None:
    """@media (max-width:600px) no <style>."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "max-width:600px" in html


def test_heading_rendered() -> None:
    """Heading aparece no HTML."""
    html = wrap_email(heading="Meu digest", body_html="<p>body</p>", footer_link="https://x")
    assert "Meu digest" in html


def test_body_html_inserted() -> None:
    """Body HTML é inserido dentro do card."""
    html = wrap_email(heading="Test", body_html="<p>CONTEUDO_AQUI</p>", footer_link="https://x")
    assert "CONTEUDO_AQUI" in html


def test_footer_link_rendered() -> None:
    """Footer link aparece no rodapé do card."""
    html = wrap_email(
        heading="Test", body_html="<p>body</p>", footer_link="https://kubo.test/distilled"
    )
    assert "https://kubo.test/distilled" in html


def test_no_external_image_references() -> None:
    """Nenhuma referência a imagem externa, anexo embutido ou arquivo hospedado."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "<img" not in html
    assert "src=" not in html
    assert "url(" not in html
    assert "cid:" not in html


def test_no_svg() -> None:
    """Sem SVG — logo é glifo de texto, email-safe."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "<svg" not in html


def test_font_is_arial_helvetica() -> None:
    """Font stack é Arial/Helvetica (email-safe), não Inter."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "Arial" in html
    assert "Helvetica" in html


def test_html_is_valid_document() -> None:
    """HTML começa com DOCTYPE e tem estrutura html/head/body."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "<head>" in html
    assert "<body" in html
    assert "</html>" in html


def test_lang_pt_br() -> None:
    """lang=pt-BR no <html>."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert 'lang="pt-BR"' in html


def test_color_scheme_meta() -> None:
    """meta color-scheme light dark para dark mode."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    assert "color-scheme" in html
    assert "light dark" in html


def test_outer_background_is_transparent() -> None:
    """Body e wrapper externo não definem cor de fundo — fica transparente."""
    html = wrap_email(heading="Test", body_html="<p>body</p>", footer_link="https://x")
    body_tag = re.search(r"<body[^>]*>", html)
    assert body_tag is not None
    assert "background-color" not in body_tag.group(0)
    outer_table = re.search(r'<table[^>]*class="kubo-bg"[^>]*>', html)
    assert outer_table is not None
    assert "background-color" not in outer_table.group(0)
