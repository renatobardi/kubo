#!/usr/bin/env python3
"""Dreno one-off do backlog de destilação (sessão 0014, gate B3) — GATED, supervisionado.

Destila o backlog (~3.2k itens sem destilado) com um modelo PAGO barato, em batches
supervisionados. Emenda pontual à D22 (ADR-0017): o regime DIÁRIO permanece Groq/free
por construção — este script NÃO toca `kubo/scheduler` nem `_DISTILLER_MODEL`. Constrói
seu PRÓPRIO `ApiExecutor` com o modelo pinado abaixo e chama `run_worker` em loop; cada
batch abre um `run` normal → proveniência e reconciliação de graça (precedente
`backfill_chunks.py`).

O modelo é uma CONSTANTE PINADA (`_DRAIN_MODEL`), não um arg de CLI: trocá-lo exige
editar aqui + PR = gate humano (invariante 5 / ADR-0010). Um arg de CLI reabriria a
porta que o hardcode fecha. O vencedor do piloto (gate B2) entra por PR nesta constante.

TETO REAL É O GEMINI (embedder), não o LLM (E5): 1 chamada de embedding/item, ~1K RPD
free → mantenha o volume por DIA sob a RPD real do Gemini (verificada no AI Studio). O
RPD do Gemini reseta meia-noite do PACÍFICO (≠ Groq UTC). Em dia de dreno, `kubo query`/
busca da UI podem tomar 429 (quota por projeto) — avisado.

CHECKLIST OPERACIONAL antes do 1º batch (runbook + plano B3):
  · spend limit configurado na key do OpenRouter · limites do Gemini verificados
  · decisão E3 (digest 09:30) tomada · JANELA 09:00–09:35 EVITADA (E4: colisão com a
    entry agendada = duplicatas reais, `insert_distilled` não é idempotente).

Uso (supervisionado, com "pode executar" por dia/pacote):
    OPENROUTER_API_KEY=... GEMINI_API_KEY=... SURREAL_URL=... \
        uv run python scripts/drain_distill.py --batch-size 25 --max-batches 10
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from kubo.embedding import GeminiEmbedder
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.runner import run_worker
from kubo.store import client, knowledge
from kubo.workers.distiller import DistillerWorker

_log = structlog.get_logger().bind(worker="drain_distill")

# MODELO DO DRENO — PINADO POR PR (gate humano). O vencedor do piloto (B2) entra AQUI.
# Candidato natural (plano 0014 B2): o próprio llama-3.3-70b via OpenRouter (~US$0,10/M →
# dreno ≈ US$1,50–2). NUNCA um modelo `groq/` free: o dreno é pago por construção e a
# conta Groq não sofre upgrade (D35). O regime diário (Groq) vive em kubo/scheduler.
_DRAIN_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct"
_DRAIN_MAX_TOKENS = 4096  # igual ao distiller diário: summary + entidades no JSON precisa de folga.

_DEFAULT_BATCH_SIZE = 25
_DEFAULT_MAX_BATCHES = 10
_DEFAULT_DELAY = 2.0


@dataclass(frozen=True)
class BatchOutcome:
    """Veredito de um batch: quantos destilou e se (e por quê) o dreno deve parar."""

    distilled: int
    stop: bool
    reason: str  # "done" | "stuck" | "error" | ""


def evaluate_batch(status: str | None, pending_before: int, pending_after: int) -> BatchOutcome:
    """Decide o próximo passo do dreno a partir do status do run e do delta de pendentes.

    - `status == 'error'` → falha SISTÊMICA (rate_limit_day/embedding_failed já persistiram
      o parcial): PARA — retentar no mesmo dia não recupera a quota.
    - `pending_after == 0` → backlog drenado: PARA com `done`.
    - `distilled <= 0` (sem progresso, mas ainda há pendentes) → o batch só encontrou itens
      malformados/vazios que não drenam: PARA com `stuck` (não queima dinheiro re-tentando
      os mesmos à toa). ponytail: sem quarentena de malformado — se `stuck` recorrer, aí
      entra marcar item malformado no schema (fora do escopo 0014).
    - senão: CONTINUA."""
    distilled = pending_before - pending_after
    if status == "error":
        return BatchOutcome(distilled, stop=True, reason="error")
    if pending_after == 0:
        return BatchOutcome(distilled, stop=True, reason="done")
    if distilled <= 0:
        return BatchOutcome(distilled, stop=True, reason="stuck")
    return BatchOutcome(distilled, stop=False, reason="")


def _build_worker() -> DistillerWorker:
    """Constrói o worker com o `ApiExecutor` do modelo PINADO do dreno (não o do scheduler)."""
    executor = ApiExecutor(ApiExecutorConfig(model=_DRAIN_MODEL, max_tokens=_DRAIN_MAX_TOKENS))
    return DistillerWorker(executor)


def drain(
    db: Any,
    *,
    batch_size: int,
    max_batches: int,
    delay: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, int, int, str]:
    """Roda até `max_batches` batches; devolve `(inicial, final, drenados, motivo)`.

    Cada batch é um `run_worker` normal (proveniência de graça). Entre batches faz
    pacing. Para cedo em erro sistêmico, backlog vazio ou stall (via `evaluate_batch`)."""
    worker = _build_worker()
    embedder = GeminiEmbedder.from_env()
    initial = pending = knowledge.count_items_without_distilled(db)
    drained = batches = 0
    reason = "max_batches"
    for _ in range(max_batches):
        if batches > 0:
            sleep(delay)
        run_id = run_worker(db, worker, config={"max_items": batch_size}, embedder=embedder)
        status = knowledge.run_status(db, run_id)
        after = knowledge.count_items_without_distilled(db)
        outcome = evaluate_batch(status, pending, after)
        drained += max(outcome.distilled, 0)
        batches += 1
        _log.info(
            "drain.batch",
            batch=batches,
            run=str(run_id),
            status=status,
            distilled=outcome.distilled,
            pending=after,
        )
        pending = after
        if outcome.stop:
            reason = outcome.reason
            break
    _log.info("drain.done", initial=initial, final=pending, drained=drained, reason=reason)
    return initial, pending, drained, reason


def main(argv: Sequence[str] | None = None) -> int:
    """CLI supervisionado: valida credenciais, imprime os lembretes operacionais e drena.

    Retorna 0 em `done`/`error`/`max_batches` (parada esperada, retomável no próximo
    "pode executar"); 1 em `stuck` (precisa atenção: itens que não drenam) ou credencial
    ausente."""
    parser = argparse.ArgumentParser(description="Dreno one-off do backlog de destilação (B3).")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-batches", type=int, default=_DEFAULT_MAX_BATCHES)
    parser.add_argument(
        "--delay", type=float, default=_DEFAULT_DELAY, help="pausa entre batches (s)"
    )
    args = parser.parse_args(argv)

    if _DRAIN_MODEL.startswith("openrouter/") and not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY ausente — o dreno é pago via OpenRouter (invariante 8).")
        return 1
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY ausente — o embedder do dreno exige a key (invariante 8).")
        return 1

    print(f"DRENO 0014 — modelo pinado: {_DRAIN_MODEL}")
    print("lembretes: spend limit no OpenRouter · Gemini RPD (E5) · EVITE 09:00–09:35 (E4).")

    with client.connect(client.config()) as db:
        initial, final, drained, reason = drain(
            db,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            delay=args.delay,
        )

    print(
        f"reconciliação: pendentes {initial} → {final} (drenados {drained}); parada: {reason}. "
        "Custo real: ver dashboard do OpenRouter (registrar no ADR-0017)."
    )
    return 1 if reason == "stuck" else 0


if __name__ == "__main__":
    raise SystemExit(main())
