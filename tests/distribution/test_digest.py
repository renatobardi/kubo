"""Builder puro do digest Telegram (ADR-0015 §IV, ADR-0050): escaping stdlib com
canários de injection e truncamento em FRONTEIRA de entry.

Segurança de primeira classe (invariante do projeto): título/summary/entidades são
conteúdo derivado de dado HOSTIL. Todo conteúdo dinâmico é escapado (`html.escape`,
mesma disciplina do XSS da 0009); só `<b>` e `<a href>` do NOSSO template são markup.
O href é o link da FONTE (`view.url`) se disponível, senão link para a UI. Truncar
dentro de `<b>`/`<a>` = HTML inválido = 400 = digest perdido = watermark não avança
= bola de neve: o corte é SEMPRE em fronteira de entry (teste obrigatório).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from kubo.contracts.worker import DigestSelectionView, DigestView
from kubo.distribution.digest import TELEGRAM_LIMIT, build_telegram_digest

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


def test_renders_title_summary_entities() -> None:
    """O digest de uma entrada traz título, resumo e entidades — em texto escapado."""
    out = build_telegram_digest(_selection([_view()]), _BASE)
    assert "OpenAI lança modelo" in out
    assert "Resumo objetivo do destilado." in out
    assert "OpenAI" in out and "GPT" in out


def test_link_points_to_source_url_when_available() -> None:
    """Quando o item tem URL da fonte, o href aponta para ela (não para a UI)."""
    out = build_telegram_digest(
        _selection([_view(key="deadbeef", url="https://example.com/article")]), _BASE
    )
    assert 'href="https://example.com/article"' in out
    assert out.count("<a ") == 1


def test_link_points_to_ui_when_no_url() -> None:
    """Sem URL da fonte, o href aponta para a UI (base_url + KEY do id, sem prefixo)."""
    out = build_telegram_digest(_selection([_view(key="deadbeef", url=None)]), _BASE)
    assert f'href="{_BASE}/item/deadbeef"' in out
    assert out.count("<a ") == 1


def test_title_is_the_hyperlink_no_separate_link_line() -> None:
    """O próprio título é o hyperlink (title-as-link) — não há linha 'abrir no Kubo'."""
    out = build_telegram_digest(
        _selection([_view(key="deadbeef", url="https://example.com/a")]), _BASE
    )
    assert '<a href="https://example.com/a"><b>OpenAI lança modelo</b></a>' in out
    assert "abrir no Kubo" not in out


def test_collected_url_in_summary_is_not_linkified() -> None:
    """Uma URL coletada no summary NÃO vira hyperlink — só texto escapado."""
    out = build_telegram_digest(_selection([_view(summary="veja http://evil.example/x")]), _BASE)
    assert out.count("<a ") == 1  # só o link da fonte/UI, não a URL do conteúdo
    assert 'href="http://evil.example' not in out


def test_escapes_markup_in_summary_canary() -> None:
    """CANÁRIO: markup no summary é escapado, não interpretado."""
    out = build_telegram_digest(
        _selection([_view(summary="<script>alert(1)</script> e <b>x</b>")]), _BASE
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;b&gt;x&lt;/b&gt;" in out


def test_escapes_markup_in_entity_name_canary() -> None:
    """CANÁRIO: nome de entidade hostil (fecha tag do nosso template) é escapado."""
    out = build_telegram_digest(_selection([_view(entities=("</a><b>pwned",))]), _BASE)
    assert "</a><b>pwned" not in out
    assert "&lt;/a&gt;&lt;b&gt;pwned" in out


def test_escapes_markup_in_title_canary() -> None:
    """CANÁRIO: markup no título é escapado."""
    out = build_telegram_digest(_selection([_view(title='<a href="evil">x</a>')]), _BASE)
    assert '<a href="evil">' not in out
    assert "&lt;a href=" in out


def test_missing_title_has_fallback() -> None:
    """Título ausente cai num rótulo neutro, sem quebrar o markup."""
    out = build_telegram_digest(_selection([_view(title=None)]), _BASE)
    assert "<b>" in out and "</b>" in out


def test_no_footer_when_all_fit() -> None:
    """Cabendo tudo, não há rodapé de truncamento."""
    out = build_telegram_digest(_selection([_view(), _view(key="x2")]), _BASE)
    assert "ver na UI" not in out


def test_truncates_at_entry_boundary_with_footer() -> None:
    """Muitas entradas estouram 4096: a saída cabe, termina com o rodapé +N, e todo
    entry renderizado está COMPLETO (nenhum corte no meio de uma entry)."""
    big = "palavra " * 60  # ~480 chars por summary → força truncamento
    views = [_view(key=f"k{i}", title=f"Titulo {i}", summary=big) for i in range(40)]
    out = build_telegram_digest(_selection(views), _BASE)
    assert len(out) <= TELEGRAM_LIMIT
    assert "itens — ver na UI" in out


def test_truncation_keeps_html_balanced() -> None:
    """Sob truncamento, as tags do nosso template ficam balanceadas (nada de tag
    aberta cortada — senão o Bot API rejeita com 400)."""
    big = "palavra " * 60
    views = [_view(key=f"k{i}", summary=big) for i in range(40)]
    out = build_telegram_digest(_selection(views), _BASE)
    assert out.count("<b>") == out.count("</b>")
    assert out.count("<a ") == out.count("</a>")


def test_footer_count_matches_omitted() -> None:
    """O +N do rodapé conta exatamente as entradas omitidas."""
    big = "palavra " * 60
    views = [_view(key=f"k{i}", summary=big) for i in range(40)]
    out = build_telegram_digest(_selection(views), _BASE)
    # nº de <b> de entry = incluídas (+1 do header); omitidas = 40 - incluídas
    entry_bolds = out.count("<b>") - 1  # header tem um <b>
    omitted = 40 - entry_bolds
    assert f"+{omitted} itens — ver na UI" in out


def test_empty_window_form_sends_warning() -> None:
    """Forma 2 (empty_window): o builder devolve um aviso curto."""
    out = build_telegram_digest(
        _selection(items=[], form="empty_window", total_publications=0), _BASE
    )
    assert "sem novidades" in out
    assert "<b>" in out


def test_none_passed_form_sends_warning_with_count() -> None:
    """Forma 3 (none_passed): o builder devolve um aviso com o número de publicações."""
    out = build_telegram_digest(
        _selection(items=[], form="none_passed", total_publications=5), _BASE
    )
    assert "5 publicações" in out
    assert "nenhuma passou o corte" in out


def test_recovery_form_has_label() -> None:
    """Forma 4 (recovery): o cabeçalho identifica a recuperação."""
    out = build_telegram_digest(_selection([_view()], form="recovery"), _BASE)
    assert "recuperação" in out
