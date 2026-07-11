#!/usr/bin/env python3
"""Backfill one-off dos 935 destilados legados (M6 marco 8.4, ADR-0013 §VI/§VII).

Script one-off, NÃO worker sob contrato (mesmo precedente de scripts/neon_import.py,
ADR-0012/D19: worker é para recorrência). Chunka+embedda o `summary` de cada
`distilled` legado (import Neon, ADR-0012) que ainda não tem chunk e anexa via
`knowledge.attach_chunks` (idempotência DENTRO da transação — ADR-0013 §VI).

Embedda-se SEMPRE o `distilled` PT-BR, nunca o `item` bruto (ADR-0013 §VII). A
tripla de proveniência é pinada por evidência (ADR-0006): model="gemini-embedding-001",
dim=768, task_type="SEMANTIC_SIMILARITY".

Inclui spot-check de idioma dos summaries legados (ADR-0013 §VII): o corpus vetorial
é 100% PT-BR por decisão (D20); summaries em EN são resíduo conhecido, reportado
para o dono decidir — não re-destilar legados nesta sessão (timebox).

Duas camadas, mesmo desenho de scripts/neon_import.py:

  1. Camada PURA (abaixo, testada em tests/scripts/test_backfill_chunks.py):
     recebe valores explícitos (textos, vetores, summaries), nunca chama rede/banco.

  2. Camada de I/O (casca): itera distilled sem chunk, chama o client de embedding
     (REST, ADR-0013 §I) e a store, gated em env (SURREAL_URL/GEMINI_API_KEY,
     invariante 8).

Uso:
    uv run python scripts/backfill_chunks.py --dry-run
    GEMINI_API_KEY=... uv run python scripts/backfill_chunks.py
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field

import structlog

from kubo.chunking import chunk_text
from kubo.embedding import GeminiEmbedder
from kubo.errors import EmbeddingError
from kubo.store import client, knowledge
from kubo.store.knowledge import Chunk

_log = structlog.get_logger().bind(worker="backfill_chunks")

# Teto de amostras guardadas por bucket (en/?) no LangReport — só o suficiente
# para o dono inspecionar o resíduo, não um dump do corpus inteiro.
_SAMPLE_CAP = 5
# Trecho truncado de cada amostra no render — summary gigante não vaza inteiro.
_SAMPLE_TRUNC = 120

# Marcadores fortes: acentuação/palavras que nunca aparecem em EN comum (PT) e
# palavras funcionais que nunca aparecem em PT (EN) — dá margem folgada mesmo
# em textos curtos, sem precisar de biblioteca de detecção de idioma.
_PT_MARKERS = {
    "que",
    "não",
    "com",
    "uma",
    "para",
    "são",
    "é",
    "dos",
    "das",
    "de",
    "e",
    "o",
    "a",
    "em",
    "por",
}
_EN_MARKERS = {
    "the",
    "and",
    "of",
    "to",
    "is",
    "are",
    "with",
    "that",
    "for",
    "in",
    "on",
    "this",
}


def build_chunks(
    texts: list[str],
    vectors: list[list[float]],
    *,
    model: str,
    dim: int,
    task_type: str,
) -> list[Chunk]:
    """Pareia texto[i] com vetor[i] num `Chunk` com seq=i e a tripla de proveniência.

    len(texts) != len(vectors) é ValueError — nunca pareia parcialmente (perder um
    chunk é perda silenciosa).
    """
    if len(texts) != len(vectors):
        raise ValueError(
            f"mismatch de tamanho: {len(texts)} textos vs {len(vectors)} vetores "
            "— pareamento parcial não é permitido (perda silenciosa de chunk)."
        )
    return [
        Chunk(text=t, seq=i, embedding=v, model=model, dim=dim, task_type=task_type)
        for i, (t, v) in enumerate(zip(texts, vectors, strict=True))
    ]


def language_guess(text: str) -> str:
    """Heurística barata PT/EN para o spot-check de idioma (ADR-0013 §VII).

    Retorna "pt", "en" ou "?" (incerto) — é triagem, não classificação perfeita.
    """
    words = text.casefold().split()
    pt_hits = sum(1 for w in words if w in _PT_MARKERS)
    en_hits = sum(1 for w in words if w in _EN_MARKERS)
    if pt_hits == 0 and en_hits == 0:
        return "?"
    if pt_hits > en_hits:
        return "pt"
    if en_hits > pt_hits:
        return "en"
    return "?"


@dataclass
class LangReport:
    """Acumulador do spot-check de idioma (espírito de ReconReport, neon_import.py).

    Buckets "en" e "?" guardam até `_SAMPLE_CAP` amostras (distilled_id, summary
    truncado) para o dono inspecionar; "pt" (caso esperado) não guarda amostra.
    """

    pt: int = 0
    en: int = 0
    uncertain: int = 0
    samples: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: {"en": [], "?": []})

    def record(self, guess: str, *, distilled_id: str, summary: str) -> None:
        """Incrementa o contador do bucket e, para en/?, guarda amostra até o teto."""
        if guess == "pt":
            self.pt += 1
            return
        if guess == "en":
            self.en += 1
        else:
            self.uncertain += 1
        bucket = self.samples.setdefault(guess, [])
        if len(bucket) < _SAMPLE_CAP:
            bucket.append((distilled_id, summary[:_SAMPLE_TRUNC]))

    def render(self) -> str:
        """Bloco de texto legível: contagens + amostras EN/incertas (id + trecho)."""
        lines = [f"idioma: pt={self.pt} en={self.en} incerto={self.uncertain}"]
        for bucket, label in (("en", "amostras EN"), ("?", "amostras incertas")):
            samples = self.samples.get(bucket, [])
            if not samples:
                continue
            lines.append(f"{label}:")
            lines += [f"  {distilled_id}: {trecho}" for distilled_id, trecho in samples]
        return "\n".join(lines)


# ── Camada de I/O ───────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    """CLI do backfill: spot-check de idioma sempre; embedding+attach só sem
    `--dry-run` (o dono inspeciona o idioma antes de gastar quota do Gemini)."""
    parser = argparse.ArgumentParser(description="Backfill de embeddings dos destilados legados.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="só relata idioma e contagem de pendentes; não chama a API nem grava",
    )
    args = parser.parse_args(argv)

    with client.connect(client.config()) as db:
        pending = knowledge.distilled_without_chunks(db)

        report = LangReport()
        for did, summary in pending:
            report.record(language_guess(summary), distilled_id=str(did), summary=summary)
        print(report.render())
        _log.info("backfill.pending", count=len(pending))

        if args.dry_run:
            return 0

        embedder = GeminiEmbedder.from_env()
        ok = falhou = vazio = 0
        for did, summary in pending:
            texts = chunk_text(summary)
            if not texts:
                vazio += 1
                _log.warning("backfill.empty_summary", distilled=str(did))
                continue
            try:
                vectors = embedder.embed(texts)
            except EmbeddingError as exc:
                falhou += 1
                _log.warning("backfill.embed_failed", distilled=str(did), error=type(exc).__name__)
                continue
            chunks = build_chunks(
                texts,
                vectors,
                model=embedder.model,
                dim=embedder.dim,
                task_type=embedder.task_type,
            )
            knowledge.attach_chunks(db, distilled=did, chunks=chunks)
            ok += 1

        _log.info("backfill.done", ok=ok, empty=vazio, failed=falhou, total=len(pending))
        print(f"backfill concluído: ok={ok} vazio={vazio} falhou={falhou} total={len(pending)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
