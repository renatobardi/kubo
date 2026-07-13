"""Builder puro do digest Telegram (ADR-0015 §IV, E4): escaping stdlib com canários
de injection e truncamento em FRONTEIRA de entry.

Segurança de primeira classe (invariante do projeto): título/summary/entidades são
conteúdo derivado de dado HOSTIL. Todo conteúdo dinâmico é escapado (`html.escape`,
mesma disciplina do XSS da 0009); só `<b>` e `<a href>` do NOSSO template são markup.
Único href = link para a UI (`base_url` + id) — `item.url` coletada NUNCA vira
hyperlink (não existe no DigestView, garantido por construção). Truncar dentro de
`<b>`/`<a>` = HTML inválido = 400 = digest perdido = watermark não avança = bola de
neve: o corte é SEMPRE em fronteira de entry (teste obrigatório).
"""

from __future__ import annotations

from datetime import datetime, timezone

from kubo.contracts.worker import DigestView
from kubo.distribution.digest import TELEGRAM_LIMIT, build_telegram_digest

_BASE = "http://100.66.254.24:3900"


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


def test_renders_title_summary_entities() -> None:
    """O digest de uma entrada traz título, resumo e entidades — em texto escapado."""
    out = build_telegram_digest([_view()], _BASE)
    assert "OpenAI lança modelo" in out
    assert "Resumo objetivo do destilado." in out
    assert "OpenAI" in out and "GPT" in out


def test_link_points_to_ui_by_id_only() -> None:
    """O único href é o link da UI (base_url + KEY do id, sem o prefixo `distilled:`)."""
    out = build_telegram_digest([_view(key="deadbeef")], _BASE)
    assert f'href="{_BASE}/distilled/deadbeef"' in out
    assert out.count("<a ") == 1  # exatamente um link


def test_collected_url_in_summary_is_not_linkified() -> None:
    """Uma URL coletada no summary NÃO vira hyperlink — só texto escapado."""
    out = build_telegram_digest([_view(summary="veja http://evil.example/x")], _BASE)
    assert out.count("<a ") == 1  # só o link da UI, não a URL do conteúdo
    assert 'href="http://evil.example' not in out


def test_escapes_markup_in_summary_canary() -> None:
    """CANÁRIO: markup no summary é escapado, não interpretado."""
    out = build_telegram_digest([_view(summary="<script>alert(1)</script> e <b>x</b>")], _BASE)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;b&gt;x&lt;/b&gt;" in out


def test_escapes_markup_in_entity_name_canary() -> None:
    """CANÁRIO: nome de entidade hostil (fecha tag do nosso template) é escapado."""
    out = build_telegram_digest([_view(entities=("</a><b>pwned",))], _BASE)
    assert "</a><b>pwned" not in out
    assert "&lt;/a&gt;&lt;b&gt;pwned" in out


def test_escapes_markup_in_title_canary() -> None:
    """CANÁRIO: markup no título é escapado."""
    out = build_telegram_digest([_view(title='<a href="evil">x</a>')], _BASE)
    assert '<a href="evil">' not in out
    assert "&lt;a href=" in out


def test_missing_title_has_fallback() -> None:
    """Título ausente cai num rótulo neutro, sem quebrar o markup."""
    out = build_telegram_digest([_view(title=None)], _BASE)
    assert "<b>" in out and "</b>" in out


def test_no_footer_when_all_fit() -> None:
    """Cabendo tudo, não há rodapé de truncamento."""
    out = build_telegram_digest([_view(), _view(key="x2")], _BASE)
    assert "ver na UI" not in out


def test_truncates_at_entry_boundary_with_footer() -> None:
    """Muitas entradas estouram 4096: a saída cabe, termina com o rodapé +N, e todo
    entry renderizado está COMPLETO (nenhum corte no meio de uma entry)."""
    big = "palavra " * 60  # ~480 chars por summary → força truncamento
    views = [_view(key=f"k{i}", title=f"Titulo {i}", summary=big) for i in range(40)]
    out = build_telegram_digest(views, _BASE)
    assert len(out) <= TELEGRAM_LIMIT
    assert "destilados — ver na UI" in out


def test_truncation_keeps_html_balanced() -> None:
    """Sob truncamento, as tags do nosso template ficam balanceadas (nada de tag
    aberta cortada — senão o Bot API rejeita com 400)."""
    big = "palavra " * 60
    views = [_view(key=f"k{i}", summary=big) for i in range(40)]
    out = build_telegram_digest(views, _BASE)
    assert out.count("<b>") == out.count("</b>")
    assert out.count("<a ") == out.count("</a>")


def test_footer_count_matches_omitted() -> None:
    """O +N do rodapé conta exatamente as entradas omitidas."""
    big = "palavra " * 60
    views = [_view(key=f"k{i}", summary=big) for i in range(40)]
    out = build_telegram_digest(views, _BASE)
    # nº de <b> de entry = incluídas (+1 do header); omitidas = 40 - incluídas
    entry_bolds = out.count("<b>") - 1  # header tem um <b>
    omitted = 40 - entry_bolds
    assert f"+{omitted} destilados — ver na UI" in out


def test_empty_views_returns_empty() -> None:
    """Sem destilados, o builder devolve string vazia (o worker nem chega a enviar)."""
    assert build_telegram_digest([], _BASE) == ""
