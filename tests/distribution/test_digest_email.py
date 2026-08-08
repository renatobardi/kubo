"""Builder puro do digest de e-mail (ADR-0031, ADR-0050): selection → (assunto, texto, HTML).

HTML inline com identidade visual do design system (glifo sakura em tile near-black,
card com ring, MSO/Outlook compat, dark mode, preheader); conteúdo dinâmico escapado.
As quatro formas de mensagem (ADR-0050 §VI) são tratadas: normal/recovery enviam o
digest; empty_window/none_passed enviam um aviso curto.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from kubo.contracts.worker import DigestSelectionView, DigestView
from kubo.distribution.digest_email import build_email_digest

_BASE = "https://kubo.test:3900"
_NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


def _view(
    key: str = "abc123",
    title: str | None = "OpenAI lança modelo",
    summary: str = "Resumo objetivo do destilado.",
    entities: tuple[str, ...] = ("OpenAI", "GPT"),
    url: str | None = None,
    score: int = 7,
    opinion: str | None = None,
) -> DigestView:
    return DigestView(
        id=f"item:{key}",
        title=title,
        summary=summary,
        score=score,
        published_at=_NOW,
        url=url,
        entities=list(entities),
        opinion=opinion,
    )


def _selection(
    items: list[DigestView] | None = None,
    *,
    form: Literal["normal", "empty_window", "none_passed", "recovery"] = "normal",
    total_publications: int | None = None,
    day_summary: str | None = None,
) -> DigestSelectionView:
    views = items if items is not None else [_view()]
    return DigestSelectionView(
        form=form,
        items=views,
        window_start=_NOW,
        window_end=_NOW,
        watermark=_NOW,
        total_publications=total_publications if total_publications is not None else len(views),
        day_summary=day_summary,
    )


def test_empty_window_form_sends_warning() -> None:
    """Forma 2 (empty_window): o builder devolve um aviso, não None."""
    result = build_email_digest(
        _selection(items=[], form="empty_window", total_publications=0), _BASE
    )
    assert result is not None
    subject, text, html = result
    assert "sem novidades" in subject
    assert "sem novidades" in text


def test_renders_subject_and_counts() -> None:
    """Assunto pluraliza corretamente."""
    one = build_email_digest(_selection([_view()]), _BASE)
    two = build_email_digest(_selection([_view(), _view(key="x2")]), _BASE)
    assert one is not None
    assert two is not None
    assert "1 novo" in one[0]
    assert "2 novos" in two[0]


def test_html_contains_title_link_summary_entities() -> None:
    """HTML traz título (com link para a fonte), resumo e entidades."""
    result = build_email_digest(
        _selection([_view(key="deadbeef", url="https://example.com/a")]), _BASE
    )
    assert result is not None
    _, _, html = result
    assert "OpenAI lança modelo" in html
    assert "Resumo objetivo do destilado." in html
    assert "OpenAI" in html and "GPT" in html
    assert 'href="https://example.com/a"' in html


def test_text_contains_title_summary_entities() -> None:
    """Corpo textual traz título, resumo e entidades sem markup."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, text, _ = result
    assert "OpenAI lança modelo" in text
    assert "Resumo objetivo do destilado." in text
    assert "OpenAI" in text and "GPT" in text
    assert "Entidades:" in text


def test_link_points_to_ui_when_no_url() -> None:
    """Sem URL da fonte, o link aponta para a UI (base_url + key do id)."""
    result = build_email_digest(_selection([_view(key="deadbeef", url=None)]), _BASE)
    assert result is not None
    _, _, html = result
    assert 'href="https://kubo.test:3900/item/deadbeef"' in html
    assert "item:deadbeef" not in html


def test_escapes_html_injection_canary() -> None:
    """CANÁRIO: markup no título/summary/entidade é escapado no HTML."""
    result = build_email_digest(
        _selection(
            [
                _view(
                    title='<a href="evil">x</a>',
                    summary="<script>alert(1)</script>",
                    entities=("</b>pwned",),
                )
            ]
        ),
        _BASE,
    )
    assert result is not None
    _, _, html = result
    assert '<a href="evil">x</a>' not in html
    assert "<script>" not in html
    assert "</b>pwned" not in html
    assert "&lt;a href=" in html
    assert "&lt;script&gt;" in html
    assert "&lt;/b&gt;pwned" in html


def test_text_does_not_escape_html_entities() -> None:
    """Corpo textual mostra o conteúdo cru — não há interpretação de HTML no plain."""
    result = build_email_digest(
        _selection([_view(summary="veja <b>isso</b>", entities=("<OpenAI>",))]),
        _BASE,
    )
    assert result is not None
    _, text, _ = result
    assert "veja <b>isso</b>" in text
    assert "<OpenAI>" in text
    assert "&lt;" not in text


def test_none_passed_form_sends_warning_with_count() -> None:
    """Forma 3 (none_passed): o builder devolve um aviso com o número."""
    result = build_email_digest(
        _selection(items=[], form="none_passed", total_publications=3), _BASE
    )
    assert result is not None
    subject, text, _ = result
    assert "3 publicações" in subject
    assert "nenhuma passou o corte" in text


# ---------------------------------------------------------------------------
# Identidade do design system no e-mail (glifo sakura em tile, card com ring,
# MSO/Outlook compat, preheader, dark mode, mobile)
# ---------------------------------------------------------------------------


def test_no_direcao_a_amber_color() -> None:
    """Direção A rejeitada: o âmbar queimado (#b06327) não aparece no HTML."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "#b06327" not in html
    assert "b06327" not in html


def test_glyph_tile_in_header() -> None:
    """Logo = glifo ❇ (&#10047;) num tile near-black — sem <svg>, sem <img>."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "&#10047;" in html or "❇" in html
    assert "#1c1917" in html  # tile near-black
    assert "#f4c9d4" in html  # glifo rosa
    assert "<svg" not in html


def test_tagline_present() -> None:
    """Tagline do logo aparece no header do e-mail."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "The art of getting things done" in html


def test_published_datetime_per_entry() -> None:
    """Cada notícia mostra data e hora de publicação (KUBO-192, KUBO-196)."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, text, html = result
    expected = "13 jul 2026 · 00:00"
    assert expected in text
    assert expected in html


def test_dark_mode_media_query() -> None:
    """Modo escuro via @media (prefers-color-scheme: dark) no <style>."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "prefers-color-scheme: dark" in html
    assert "<style" in html


def test_dark_mode_classes_on_content_elements() -> None:
    """As classes de tema (kubo-card, kubo-fg, kubo-muted, kubo-border)
    estão presentes nos elementos renderizados para que o dark mode os atinja."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "kubo-card" in html
    assert "kubo-fg" in html
    assert "kubo-muted" in html
    assert "kubo-border" in html
    # kubo-border aplicado aos divisores entre entries (CodeRabbit review)
    assert html.count('class="kubo-border"') >= 2  # divisor do template + divisor entre entries


def test_mobile_media_query() -> None:
    """Variante mobile legível via @media (max-width: 600px)."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "max-width:600px" in html or "max-width: 600px" in html


def test_no_external_image_references() -> None:
    """Nenhuma referência a imagem externa, anexo embutido ou arquivo hospedado."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "<img" not in html
    assert "src=" not in html  # sem src= em qualquer tag
    assert "url(" not in html
    assert "cid:" not in html


def test_rendered_weight_under_cutoff() -> None:
    """Peso do e-mail com 10 notícias permanece bem abaixo do teto de 102KB do Gmail."""
    items = [_view(key=f"item{i}") for i in range(10)]
    result = build_email_digest(_selection(items), _BASE)
    assert result is not None
    _, _, html = result
    assert len(html.encode("utf-8")) < 102_400  # 102KB em bytes


def test_warning_email_uses_brand_identity() -> None:
    """E-mail de aviso (empty_window/none_passed) também usa a identidade do design system."""
    result = build_email_digest(
        _selection(items=[], form="empty_window", total_publications=0), _BASE
    )
    assert result is not None
    _, _, html = result
    assert "&#10047;" in html or "❇" in html
    assert "The art of getting things done" in html
    assert "b06327" not in html


def test_preheader_present() -> None:
    """Preheader hidden no topo do e-mail (preview text em clientes de email)."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "display:none" in html
    assert "max-height:0" in html


def test_mso_conditional_comments() -> None:
    """Comentários condicionais MSO para compatibilidade com Outlook desktop."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "[if mso]" in html


def test_card_uses_ring_not_border() -> None:
    """O card usa box-shadow inset (ring) em vez de border — assinatura do design system."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "box-shadow:inset 0 0 0 1px" in html or "box-shadow:inset 0 0 0 1px #e7e5e4" in html


# ---------------------------------------------------------------------------
# Spec review PR #227 — cobertura faltante (AC5, AC6, AC7, AC9)
# ---------------------------------------------------------------------------


def test_opinion_rendered_in_html_and_text() -> None:
    """AC5: cada notícia mostra o parecer opinativo no HTML e no texto."""
    view = _view(opinion="Vale a pena ler: o modelo é consistente.")
    result = build_email_digest(_selection([view]), _BASE)
    assert result is not None
    _, text, html = result
    assert "Parecer: Vale a pena ler: o modelo é consistente." in text
    assert "Parecer:" in html
    assert "Vale a pena ler: o modelo é consistente." in html


def test_day_summary_rendered_at_top() -> None:
    """AC6: o resumo do dia aparece no topo do e-mail, antes das entries."""
    summary = "Dia marcado por anúncios de IA e movimentação no Congresso."
    result = build_email_digest(_selection([_view()], day_summary=summary), _BASE)
    assert result is not None
    _, _, html = result
    assert summary in html
    # O day_summary vem antes da primeira entry (título da notícia)
    assert html.index(summary) < html.index("OpenAI lança modelo")


def test_dark_mode_classes_on_correct_elements() -> None:
    """AC7: as classes de tema estão nos elementos corretos, não soltas no HTML."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    # kubo-card deve estar no <td> do card (com box-shadow inset)
    assert 'class="kubo-card"' in html
    # kubo-fg deve estar em elementos de texto (wordmark, título, link)
    assert 'class="kubo-fg"' in html
    # kubo-muted deve estar em elementos de texto secundário (date, summary, opinion)
    assert 'class="kubo-muted"' in html


def test_day_summary_is_escaped() -> None:
    """AC9: conteúdo hostil no day_summary é escapado antes de renderizar."""
    hostile_summary = '<script>alert("xss")</script>Resumo do dia.'
    result = build_email_digest(_selection([_view()], day_summary=hostile_summary), _BASE)
    assert result is not None
    _, _, html = result
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "alert" in html  # texto visível, não executável
