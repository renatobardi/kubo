"""Builder puro do digest de e-mail (ADR-0031, ADR-0050): selection → (assunto, texto, HTML).

HTML inline com identidade visual mínima; conteúdo dinâmico escapado. As quatro
formas de mensagem (ADR-0050 §VI) são tratadas: normal/recovery enviam o digest;
empty_window/none_passed enviam um aviso curto.
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
) -> DigestView:
    return DigestView(
        id=f"item:{key}",
        title=title,
        summary=summary,
        score=score,
        published_at=_NOW,
        url=url,
        entities=list(entities),
    )


def _selection(
    items: list[DigestView] | None = None,
    *,
    form: Literal["normal", "empty_window", "none_passed", "recovery"] = "normal",
    total_publications: int | None = None,
) -> DigestSelectionView:
    views = items if items is not None else [_view()]
    return DigestSelectionView(
        form=form,
        items=views,
        window_start=_NOW,
        window_end=_NOW,
        watermark=_NOW,
        total_publications=total_publications if total_publications is not None else len(views),
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
# KUBO-196 — Identidade Direção B v2 no e-mail
# ---------------------------------------------------------------------------


def test_no_direcao_a_amber_color() -> None:
    """Direção A rejeitada: o âmbar queimado (#b06327) não aparece no HTML."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "#b06327" not in html
    assert "b06327" not in html


def test_sakura_svg_inline_in_header() -> None:
    """Logo sakura como SVG inline no header — sem <img>, sem src=, sem url()."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "<svg" in html
    assert "viewBox" in html
    # 5 pétalas = 5 paths com o path da pétala
    assert html.count("<path") >= 5


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
    _, _, html = result
    # _NOW = datetime(2026, 7, 13, tzinfo=timezone.utc) → "13 jul 2026 · 14:30"
    # (UTC 00:00 = 14:30 não; _NOW é meia-noite UTC → "00:00")
    assert "13 jul 2026" in html


def test_dark_mode_media_query() -> None:
    """Modo escuro via @media (prefers-color-scheme: dark) no <style>."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "prefers-color-scheme: dark" in html
    assert "<style" in html


def test_mobile_media_query() -> None:
    """Variante mobile legível via @media (max-width: 600px)."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "max-width: 600px" in html


def test_no_external_image_references() -> None:
    """Nenhuma referência a imagem externa, anexo embutido ou arquivo hospedado."""
    result = build_email_digest(_selection([_view()]), _BASE)
    assert result is not None
    _, _, html = result
    assert "<img" not in html
    assert "src=" not in html  # sem src= em qualquer tag (svg usa path, não src)
    assert "url(" not in html
    assert "cid:" not in html


def test_rendered_weight_under_cutoff() -> None:
    """Peso do e-mail com 10 notícias permanece bem abaixo do teto de 102KB do Gmail."""
    items = [_view(key=f"item{i}") for i in range(10)]
    result = build_email_digest(_selection(items), _BASE)
    assert result is not None
    _, _, html = result
    assert len(html.encode("utf-8")) < 102_400  # 102KB em bytes


def test_warning_email_uses_direcao_b_identity() -> None:
    """E-mail de aviso (empty_window/none_passed) também usa a identidade B v2."""
    result = build_email_digest(
        _selection(items=[], form="empty_window", total_publications=0), _BASE
    )
    assert result is not None
    _, _, html = result
    assert "<svg" in html
    assert "The art of getting things done" in html
    assert "b06327" not in html
