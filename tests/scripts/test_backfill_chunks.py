"""Camada pura do backfill de embeddings dos legados (M6 marco 8.4, ADR-0013 §VII).

Testa SÓ pareamento texto/vetor -> Chunk e a heurística de idioma do spot-check,
com valores explícitos — sem rede, sem SurrealDB (mesmo desenho de
tests/scripts/test_neon_import.py). A casca de I/O (client de embedding real,
iteração sobre distilled sem chunk, attach_chunks) é validada na sessão de
execução, contra dependências vivas (docstring do módulo).
"""

from __future__ import annotations

import pytest

from kubo.store.knowledge import Chunk
from scripts import backfill_chunks as bc

_MODEL = "gemini-embedding-001"
_DIM = 768
_TASK_TYPE = "SEMANTIC_SIMILARITY"


# ── build_chunks ────────────────────────────────────────────────────────────


def test_build_chunks_pairs_text_and_vector_with_seq_and_provenance() -> None:
    """Cada texto[i]/vetor[i] vira um Chunk com seq=i e a tripla carimbada."""
    chunks = bc.build_chunks(
        ["a", "b"],
        [[0.1, 0.2], [0.3, 0.4]],
        model="m",
        dim=2,
        task_type="SEMANTIC_SIMILARITY",
    )
    assert chunks == [
        Chunk(
            text="a",
            seq=0,
            embedding=[0.1, 0.2],
            model="m",
            dim=2,
            task_type="SEMANTIC_SIMILARITY",
        ),
        Chunk(
            text="b",
            seq=1,
            embedding=[0.3, 0.4],
            model="m",
            dim=2,
            task_type="SEMANTIC_SIMILARITY",
        ),
    ]


def test_build_chunks_with_pinned_triple_stamps_every_chunk() -> None:
    """A tripla pinada (ADR-0006) é carimbada em TODOS os chunks, não só no primeiro."""
    chunks = bc.build_chunks(
        ["x", "y", "z"],
        [[0.0], [0.1], [0.2]],
        model=_MODEL,
        dim=_DIM,
        task_type=_TASK_TYPE,
    )
    assert [c.seq for c in chunks] == [0, 1, 2]
    assert all(c.model == _MODEL and c.dim == _DIM and c.task_type == _TASK_TYPE for c in chunks)


def test_build_chunks_empty_lists_returns_empty() -> None:
    """Sem texto/vetor, lista vazia — não é erro (distilled já pode ter zero chunks)."""
    assert bc.build_chunks([], [], model=_MODEL, dim=_DIM, task_type=_TASK_TYPE) == []


def test_build_chunks_length_mismatch_raises_value_error() -> None:
    """Descasamento de tamanho nunca pareia parcialmente — perder um chunk é perda
    silenciosa (ADR-0013 §VII: retomabilidade não pode mascarar bug de pareamento)."""
    with pytest.raises(ValueError, match="tamanho|length|mismatch"):
        bc.build_chunks(["a", "b"], [[0.1, 0.2]], model=_MODEL, dim=_DIM, task_type=_TASK_TYPE)


# ── language_guess ───────────────────────────────────────────────────────────


def test_language_guess_detects_clear_portuguese() -> None:
    """Texto PT-BR com marcadores inequívocos -> "pt"."""
    text = (
        "O agente coletou notícias de várias fontes e destilou o conteúdo em "
        "português para que os leitores não perdessem nenhuma novidade importante."
    )
    assert bc.language_guess(text) == "pt"


def test_language_guess_detects_clear_english() -> None:
    """Texto EN com marcadores inequívocos -> "en" (resíduo do legado, ADR-0013 §VII)."""
    text = (
        "The agent collected news from several sources and distilled the content "
        "into English so that readers would not miss any important update."
    )
    assert bc.language_guess(text) == "en"


def test_language_guess_returns_uncertain_for_empty_or_signal_free_text() -> None:
    """Sem sinais de nenhum idioma (vazio ou só números) -> "?" (triagem, não força
    veredito quando não há evidência)."""
    assert bc.language_guess("") == "?"
    assert bc.language_guess("123 456 789") == "?"


# ── LangReport ────────────────────────────────────────────────────────────────


def test_lang_report_counts_each_bucket() -> None:
    """record() incrementa o contador do bucket certo (pt/en/uncertain)."""
    report = bc.LangReport()
    for i in range(3):
        report.record("pt", distilled_id=f"distilled:pt{i}", summary="resumo em português")
    for i in range(2):
        report.record("en", distilled_id=f"distilled:en{i}", summary="summary in english")
    report.record("?", distilled_id="distilled:u0", summary="123")
    assert report.pt == 3
    assert report.en == 2
    assert report.uncertain == 1


def test_lang_report_render_includes_english_sample() -> None:
    """Uma amostra EN registrada aparece no render (id + trecho) para o dono inspecionar."""
    report = bc.LangReport()
    report.record("en", distilled_id="distilled:abc123", summary="a clearly english legacy summary")
    out = report.render()
    assert "distilled:abc123" in out
    assert "clearly english legacy summary" in out


def test_lang_report_render_excludes_portuguese_samples() -> None:
    """Bucket "pt" é o caso esperado — não vira amostra no render (só contagem)."""
    report = bc.LangReport()
    report.record(
        "pt", distilled_id="distilled:pt-nao-deve-aparecer", summary="um resumo normal em pt"
    )
    out = report.render()
    assert "distilled:pt-nao-deve-aparecer" not in out


def test_lang_report_render_truncates_long_summary_sample() -> None:
    """Summary gigante na amostra é truncado (ex.: 120 chars) — não vaza inteiro no render."""
    report = bc.LangReport()
    long_summary = "x" * 5000
    report.record("en", distilled_id="distilled:long1", summary=long_summary)
    out = report.render()
    assert long_summary not in out


def test_lang_report_caps_samples_per_bucket() -> None:
    """Teto de amostras por bucket (ex.: 5) — registrar 10 EN guarda no máximo 5,
    mesmo com todos os contadores corretamente incrementados."""
    report = bc.LangReport()
    for i in range(10):
        report.record("en", distilled_id=f"distilled:en{i}", summary=f"english summary {i}")
    assert report.en == 10
    assert len(report.samples["en"]) <= 5
    assert len(report.samples["en"]) >= 1
