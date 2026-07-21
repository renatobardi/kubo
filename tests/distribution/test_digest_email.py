"""Builder puro do digest de e-mail (ADR-0031): views → (assunto, texto, HTML).

HTML inline com identidade visual mínima; conteúdo dinâmico escapado. Sem novidade
o builder devolve `None` para que o worker nem abra o sender.
"""

from __future__ import annotations

from datetime import datetime, timezone

from kubo.contracts.worker import DigestView
from kubo.distribution.digest_email import build_email_digest

_BASE = "https://kubo.test:3900"


def _view(
    key: str = "abc123",
    title: str | None = "OpenAI lança modelo",
    summary: str = "Resumo objetivo do destilado.",
    entities: tuple[str, ...] = ("OpenAI", "GPT"),
) -> DigestView:
    return DigestView(
        id=f"distilled:{key}",
        title=title,
        summary=summary,
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        entities=list(entities),
    )


def test_empty_views_returns_none() -> None:
    """Sem destilados, o builder devolve None (não gera e-mail vazio)."""
    assert build_email_digest([], _BASE) is None


def test_renders_subject_and_counts() -> None:
    """Assunto pluraliza corretamente."""
    one = build_email_digest([_view()], _BASE)
    two = build_email_digest([_view(), _view(key="x2")], _BASE)
    assert one is not None
    assert two is not None
    assert "1 novo" in one[0]
    assert "2 novos" in two[0]


def test_html_contains_title_link_summary_entities() -> None:
    """HTML traz título (com link para UI), resumo e entidades."""
    result = build_email_digest([_view(key="deadbeef")], _BASE)
    assert result is not None
    _, _, html = result
    assert "OpenAI lança modelo" in html
    assert "Resumo objetivo do destilado." in html
    assert "OpenAI" in html and "GPT" in html
    assert 'href="https://kubo.test:3900/distilled/deadbeef"' in html


def test_text_contains_title_summary_entities() -> None:
    """Corpo textual traz título, resumo e entidades sem markup."""
    result = build_email_digest([_view()], _BASE)
    assert result is not None
    _, text, _ = result
    assert "OpenAI lança modelo" in text
    assert "Resumo objetivo do destilado." in text
    assert "OpenAI" in text and "GPT" in text
    assert "Entidades:" in text


def test_link_points_to_ui_by_id_only() -> None:
    """O link da UI usa a key do id (sem prefixo `distilled:`)."""
    result = build_email_digest([_view(key="deadbeef")], _BASE)
    assert result is not None
    _, _, html = result
    assert result is not None
    assert 'href="https://kubo.test:3900/distilled/deadbeef"' in html
    assert "distilled:deadbeef" not in html


def test_escapes_html_injection_canary() -> None:
    """CANÁRIO: markup no título/summary/entidade é escapado no HTML."""
    result = build_email_digest(
        [
            _view(
                title='<a href="evil">x</a>',
                summary="<script>alert(1)</script>",
                entities=("</b>pwned",),
            )
        ],
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
        [_view(summary="veja <b>isso</b>", entities=("<OpenAI>",))],
        _BASE,
    )
    assert result is not None
    _, text, _ = result
    assert "veja <b>isso</b>" in text
    assert "<OpenAI>" in text
    assert "&lt;" not in text
