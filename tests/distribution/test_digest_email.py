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
