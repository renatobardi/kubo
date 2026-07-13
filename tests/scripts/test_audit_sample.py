"""Camada pura da amostra de auditoria do dreno (sessão 0014, gate B1).

Testa SÓ classificação de estrato, seleção com cotas e render do markdown — com
valores explícitos, sem rede e sem SurrealDB (mesmo desenho de
tests/scripts/test_backfill_chunks.py). A casca de I/O (list_distilled_with_items,
items_by_ids, escrita do doc) é exercida na sessão de execução contra o banco vivo.
"""

from __future__ import annotations

from scripts import audit_sample as aud

_PT = "O banco central manteve a taxa de juros nesta reunião para observar mais dados."
_EN = "The central bank kept interest rates unchanged in this meeting to watch more data."


def test_classify_recent_when_produced_by_distiller() -> None:
    """run_worker == 'distiller' é RECENTE, independente do idioma do summary."""
    assert aud.classify("distiller", _EN) == "recent"


def test_classify_legacy_pt_and_en_by_language() -> None:
    """Sem run de distiller, o estrato vem do idioma do summary (heurística de stopwords)."""
    assert aud.classify(None, _PT) == "legacy_pt"
    assert aud.classify(None, _EN) == "legacy_en"


def test_classify_non_distiller_run_is_legacy_not_recent() -> None:
    """Um run de outro worker (ex.: import legado carimbado) NÃO é recente — o
    discriminador é `== 'distiller'`, não `has_run`."""
    assert aud.classify("neon_import", _PT) == "legacy_pt"


def test_classify_uncertain_language_returns_none() -> None:
    """Summary sem sinal de idioma (nem PT nem EN) não entra na amostra legada."""
    assert aud.classify(None, "xyz 123 %%%") is None


def test_select_sample_respects_quotas_per_stratum() -> None:
    """As cotas 8+4+4 são tetos por estrato; excedente do mesmo estrato é descartado."""
    rows = (
        [(f"distilled:r{i}", _PT, f"item:r{i}", "2026-07-10", "distiller") for i in range(10)]
        + [(f"distilled:p{i}", _PT, f"item:p{i}", "2026-01-01", None) for i in range(6)]
        + [(f"distilled:e{i}", _EN, f"item:e{i}", "2026-01-01", None) for i in range(6)]
    )
    picked = aud.select_sample(rows)
    counts = {s: sum(1 for c in picked if c.stratum == s) for s in aud.STRATA}
    assert counts == {"recent": 8, "legacy_pt": 4, "legacy_en": 4}


def test_select_sample_skips_rows_without_item_id() -> None:
    """Destilado sem item de origem (item_id None) é pulado — sem content para comparar."""
    rows = [
        ("distilled:1", _PT, None, "2026-07-10", "distiller"),
        ("distilled:2", _PT, "item:2", "2026-07-10", "distiller"),
    ]
    picked = aud.select_sample(rows)
    assert [str(c.distilled_id) for c in picked] == ["distilled:2"]


def test_select_sample_preserves_row_order() -> None:
    """A seleção preserva a ordem das linhas (mais-recentes-primeiro vinda da store)."""
    rows = [(f"distilled:r{i}", _PT, f"item:r{i}", "2026-07-10", "distiller") for i in range(3)]
    picked = aud.select_sample(rows)
    assert [str(c.item_id) for c in picked] == ["item:r0", "item:r1", "item:r2"]


def test_render_doc_shows_summary_and_content_and_rubric() -> None:
    """O doc traz o summary, o content e o stub da rubrica de cada item."""
    cand = aud.Candidate("distilled:1", "resumo do item", "item:1", "2026-07-10", "recent")
    doc = aud.render_doc([(cand, "conteúdo original do item")])
    assert "resumo do item" in doc
    assert "conteúdo original do item" in doc
    assert "Alucinação" in doc
    assert "distilled:1" in doc


def test_render_doc_truncates_content_at_cap() -> None:
    """Content maior que o cap é truncado (o LLM viu só o cap) e marcado como truncado."""
    cand = aud.Candidate("distilled:1", "resumo", "item:1", "2026-07-10", "recent")
    doc = aud.render_doc([(cand, "x" * 100)], input_char_cap=10)
    assert "x" * 10 in doc
    assert "x" * 11 not in doc
    assert "truncado" in doc
