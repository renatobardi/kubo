#!/usr/bin/env python3
"""Piloto lado a lado do dreno (sessão 0014, gate B2) — GATED, doc NÃO commitável.

Roda os MESMOS itens da amostra de auditoria (8+4+4) por um ou mais modelos
CANDIDATOS via LiteLLM (ex.: `openrouter/...` — só env key, ZERO mudança no
executor) e monta um doc lado a lado: content × summary do llama-3.3 já gravado
(baseline) × summary de cada candidato. O dono aponta o vencedor.

Template do `distiller_smoke.py`: `ApiExecutor.complete` + `filter_present_entities`,
SEM `run_worker`, SEM persistência, SEM embedding — é só geração para comparação
visual. Conta `malformed`/`rate_limited`/`provider_errors` por candidato: um modelo
com 10% malformado encarece o dreno em re-runs (sinal de custo, não só qualidade).

O modelo do PILOTO é livre (`--models`) — a exploração é o ponto. O PIN do modelo
do dreno mora em `drain_distill.py` (gate humano por PR), não aqui.

O doc contém CONTEÚDO COLETADO — NÃO commitável. Default git-ignorado (`.local.md`).

Uso:
    OPENROUTER_API_KEY=... uv run python scripts/distill_pilot.py \
        --models openrouter/meta-llama/llama-3.3-70b-instruct
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from kubo.errors import ExecutorError, MalformedOutputError, RateLimitExhausted
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.executors.base import Executor
from kubo.store import client, knowledge
from kubo.workers.distiller import _INSTRUCTION, DistillOutput, filter_present_entities
from scripts.audit_sample import select_sample

_log = structlog.get_logger().bind(worker="distill_pilot")

_DEFAULT_MODELS = ["openrouter/meta-llama/llama-3.3-70b-instruct"]
_DEFAULT_DELAY = 1.0
_PILOT_MAX_TOKENS = 4096  # igual ao distiller: summary + entidades no JSON precisa de folga.
_INPUT_CHAR_CAP = 20000
_DEFAULT_OUT = "distill_pilot.local.md"  # git-ignorado (.local.md): conteúdo coletado.


@dataclass
class PilotResult:
    """Saída do piloto para um modelo candidato: summary por item + contadores."""

    model: str
    summaries: dict[str, str | None] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    malformed: int = 0
    rate_limited: int = 0
    provider_errors: int = 0


def run_candidate(
    model: str,
    items: Sequence[tuple[str, str]],
    *,
    executor: Executor | None = None,
    delay: float = _DEFAULT_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> PilotResult:
    """Roda cada `(key, content)` de `items` por `model` e agrega um `PilotResult`.

    `executor` é injetável (seam de teste); default None constrói um `ApiExecutor`
    real. Falha por-item (malformado/rate-limit/provider) é contada e o summary do
    item fica None — o piloto nunca explode, o doc mostra a lacuna. Pacing: `sleep`
    entre itens (nunca antes do 1º). Aplica `filter_present_entities` (pipeline real,
    não saída crua) — só o COUNT de entidades entra no doc, nunca os nomes (§VIII)."""
    exec_ = executor if executor is not None else _build_executor(model)
    result = PilotResult(model=model)
    for index, (key, content) in enumerate(items):
        if index > 0:
            sleep(delay)
        capped = content[:_INPUT_CHAR_CAP]
        try:
            out = exec_.complete(_INSTRUCTION, capped, DistillOutput)
        except MalformedOutputError:
            result.malformed += 1
            result.summaries[key] = None
            continue
        except RateLimitExhausted:
            result.rate_limited += 1
            result.summaries[key] = None
            continue
        except ExecutorError:
            result.provider_errors += 1
            result.summaries[key] = None
            continue
        result.summaries[key] = out.summary
        result.entity_counts[key] = len(filter_present_entities(out.entities, capped))
    return result


def _build_executor(model: str) -> ApiExecutor:
    """Constrói o `ApiExecutor` real do candidato (max_tokens igual ao distiller)."""
    return ApiExecutor(ApiExecutorConfig(model=model, max_tokens=_PILOT_MAX_TOKENS))


def render_pilot(
    items: Sequence[tuple[str, str]],
    baselines: dict[str, str],
    results: Sequence[PilotResult],
    *,
    input_char_cap: int = _INPUT_CHAR_CAP,
) -> str:
    """Monta o markdown: por item, content truncado + summary baseline (llama gravado)
    + summary de cada candidato; rodapé com contadores por candidato.

    `items` são `(key, content)`; `baselines[key]` é o summary do llama já no banco."""
    parts = [
        "# Piloto do dreno 0014 (gate B2) — content × baseline (llama) × candidatos\n\n"
        "> **Conteúdo coletado — NÃO commitar.** Aponte o vencedor; o pin vai por PR.\n\n"
    ]
    for idx, (key, content) in enumerate(items, start=1):
        shown = content[:input_char_cap]
        trunc = "  _(truncado ao cap)_" if len(content) > input_char_cap else ""
        block = [
            f"\n## {idx}. item `{key}`\n\n",
            f"### Content (cap {input_char_cap}){trunc}\n\n```\n{shown}\n```\n\n",
            f"### Baseline (llama-3.3, gravado)\n\n{baselines.get(key, '—')}\n\n",
        ]
        for res in results:
            summary = res.summaries.get(key)
            ents = res.entity_counts.get(key)
            tag = summary if summary is not None else "_(falhou: malformado/limite/provider)_"
            ent_line = f"  · entidades mantidas: {ents}" if ents is not None else ""
            block.append(f"### Candidato `{res.model}`{ent_line}\n\n{tag}\n\n")
        parts.append("".join(block))
    parts.append("\n---\n\n## Contadores por candidato\n\n")
    for res in results:
        ok = sum(1 for v in res.summaries.values() if v is not None)
        parts.append(
            f"- `{res.model}`: ok={ok} malformed={res.malformed} "
            f"rate_limited={res.rate_limited} provider_errors={res.provider_errors}\n"
        )
    return "".join(parts)


# ── Camada de I/O ───────────────────────────────────────────────────────────


def _load_sample(db: Any) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Reconstrói a MESMA amostra da auditoria e casa cada item com seu content e
    o baseline (summary do llama gravado). Devolve `(items, baselines)`."""
    rows = knowledge.list_distilled_with_items(db, limit=10000)
    sample = select_sample(rows)
    contents = {
        str(i): content
        for i, _title, content in knowledge.items_by_ids(db, [c.item_id for c in sample])
    }
    items = [(str(c.item_id), contents.get(str(c.item_id), "")) for c in sample]
    baselines = {str(c.item_id): c.summary for c in sample}
    return items, baselines


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: exige OPENROUTER_API_KEY (ou a key do provider dos --models), roda os
    candidatos sobre a amostra e grava o doc. Imprime só contadores (nunca o conteúdo)."""
    parser = argparse.ArgumentParser(description="Piloto lado a lado do dreno (gate B2).")
    parser.add_argument(
        "--models",
        default=None,
        help="modelos candidatos separados por vírgula (default: llama-3.3 via OpenRouter)",
    )
    parser.add_argument("--out", default=_DEFAULT_OUT, help=f"saída (default {_DEFAULT_OUT})")
    parser.add_argument("--delay", type=float, default=_DEFAULT_DELAY, help="pausa entre itens (s)")
    args = parser.parse_args(argv)

    models = args.models.split(",") if args.models else list(_DEFAULT_MODELS)
    if not os.environ.get("OPENROUTER_API_KEY") and any(
        m.startswith("openrouter/") for m in models
    ):
        print("OPENROUTER_API_KEY ausente — candidato OpenRouter requer a key (inv. 8).")
        return 2

    with client.connect(client.config()) as db:
        items, baselines = _load_sample(db)

    results = [run_candidate(model, items, delay=args.delay) for model in models]
    doc = render_pilot(items, baselines, results)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(doc)

    for res in results:
        ok = sum(1 for v in res.summaries.values() if v is not None)
        _log.info(
            "pilot.model",
            model=res.model,
            ok=ok,
            malformed=res.malformed,
            rate_limited=res.rate_limited,
            provider_errors=res.provider_errors,
        )
    print(f"piloto em {args.out} (NÃO commitar): {len(items)} itens, {len(models)} modelo(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
