#!/usr/bin/env python3
"""Amostra de auditoria do dreno (sessão 0014, gate B1) — GATED, doc NÃO commitável.

Gera um doc markdown lado a lado (summary do destilado × content do item truncado
no MESMO `input_char_cap`=20k que o LLM viu) para o dono julgar a QUALIDADE do que
o llama-3.3 já destilou — antes de gastar dinheiro no dreno pago. Amostra
estratificada 8+4+4: 8 distiller-recente (decide o dreno) + 4 legado PT + 4 legado
EN (decidem re-destilação futura). O discriminador recente-vs-legado é o
`run_worker` do `produced_by` (store, A4); idioma dos legados por heurística de
stopwords (reusada do backfill).

O doc contém CONTEÚDO COLETADO — NÃO é commitável (plano 0014 B1). O default de
saída é `audit_sample.local.md` (git-ignorado); só o AGREGADO (contagens +
veredito do dono) entra nas notas da sessão.

Duas camadas (mesmo desenho de scripts/backfill_chunks.py):

  1. Camada PURA (testada em tests/scripts/test_audit_sample.py): classificação de
     estrato, seleção com cotas e render do markdown — valores explícitos, sem rede.

  2. Camada de I/O (casca): lê o acervo via `knowledge.list_distilled_with_items` +
     `knowledge.items_by_ids` (invariante 2 — nenhuma query crua aqui) e grava o doc.

Uso:
    uv run python scripts/audit_sample.py                 # grava audit_sample.local.md
    uv run python scripts/audit_sample.py --out /tmp/a.md --cap 20000
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from kubo.store import client, knowledge
from scripts.backfill_chunks import language_guess

_log = structlog.get_logger().bind(worker="audit_sample")

_INPUT_CHAR_CAP = 20000  # espelha DistillerConfig.input_char_cap: o content como o LLM o viu.
# git-ignorado (.local.md): conteúdo coletado, não commitar.
_DEFAULT_OUT = "audit_sample.local.md"

# Cotas por estrato (plano 0014 B1): 8 recente decide o dreno; 4+4 legados decidem
# re-destilação futura (fora do dreno). A agregação julga TENDÊNCIA por estrato.
_QUOTAS = {"recent": 8, "legacy_pt": 4, "legacy_en": 4}
STRATA = ("recent", "legacy_pt", "legacy_en")

_STRATUM_LABEL = {
    "recent": "distiller-recente (decide o dreno)",
    "legacy_pt": "legado PT (re-destilação futura)",
    "legacy_en": "legado EN (re-destilação futura)",
}


@dataclass(frozen=True)
class Candidate:
    """Um destilado selecionado para a auditoria, com seu estrato já classificado.

    `distilled_id`/`item_id` guardam o objeto cru da store (RecordID em produção,
    str nos testes) — o pareamento com o content acontece por esse id na casca."""

    distilled_id: Any
    summary: str
    item_id: Any
    created_at: str
    stratum: str


def classify(run_worker: str | None, summary: str) -> str | None:
    """Classifica um destilado em `recent`/`legacy_pt`/`legacy_en`, ou None se
    inservível para a amostra.

    `recent` = produzido por um run do worker `distiller` (o alvo do gate: é o que o
    dreno vai gerar mais). Caso contrário é legado (import Neon, sem produced_by ou
    com outro worker), sub-classificado pelo idioma do summary. Idioma incerto ('?')
    não entra: os estratos legados são explicitamente por idioma."""
    if run_worker == "distiller":
        return "recent"
    guess = language_guess(summary)
    if guess == "pt":
        return "legacy_pt"
    if guess == "en":
        return "legacy_en"
    return None


def select_sample(
    rows: Sequence[tuple[Any, str, Any, str, str | None]],
    *,
    quotas: dict[str, int] | None = None,
) -> list[Candidate]:
    """Seleciona até a cota de cada estrato, preservando a ordem das linhas
    (mais-recentes-primeiro vinda da store).

    Destilado sem `item_id` (proveniência incompleta) é pulado — sem item de origem
    não há content para comparar com o summary. Preencher menos que a cota é OK: a
    rubrica julga tendência por estrato, não caso isolado (plano 0014 B1)."""
    quotas = quotas if quotas is not None else _QUOTAS
    counts: dict[str, int] = dict.fromkeys(STRATA, 0)
    picked: list[Candidate] = []
    for distilled_id, summary, item_id, created_at, run_worker in rows:
        if item_id is None:
            continue
        stratum = classify(run_worker, summary)
        if stratum is None or counts[stratum] >= quotas.get(stratum, 0):
            continue
        counts[stratum] += 1
        picked.append(Candidate(distilled_id, summary, item_id, created_at, stratum))
    return picked


def _rubric_header(counts: dict[str, int]) -> str:
    """Bloco de topo: rubrica fixada (plano 0014 B1) + contagem obtida por estrato."""
    got = " · ".join(f"{_STRATUM_LABEL[s]}: {counts.get(s, 0)}/{_QUOTAS[s]}" for s in STRATA)
    return (
        "# Auditoria de qualidade — dreno 0014 (gate B1)\n\n"
        "> **Conteúdo coletado — NÃO commitar.** Só o agregado entra nas notas.\n\n"
        "## Rubrica (por item)\n\n"
        "- **Alucinação** (binário, ELIMINATÓRIO): o summary afirma algo que NÃO está no content?\n"
        "- **Fidelidade** · **PT-BR natural** · **Entidades**: aprova · ressalva · reprova.\n"
        "- **Nota livre**.\n\n"
        "## Agregação (por ESTRATO)\n\n"
        "- 1 alucinação no estrato ⇒ estrato **reprovado**.\n"
        "- ≥80% aprova ⇒ estrato **ok**. Julgue a TENDÊNCIA, não o caso isolado.\n"
        "- Estrato `recente` reprovado por alucinação ⇒ **GO do dreno morre** até novo piloto.\n\n"
        f"**Amostra obtida:** {got}\n\n---\n"
    )


def render_doc(
    entries: Sequence[tuple[Candidate, str]],
    *,
    input_char_cap: int = _INPUT_CHAR_CAP,
) -> str:
    """Monta o markdown lado a lado (summary × content truncado) por estrato.

    `entries` são pares `(candidate, item_content)`. Cada item mostra o summary, o
    content truncado ao MESMO cap do LLM (com marca de truncagem) e um stub da
    rubrica para o dono marcar. Nunca reordena: respeita a ordem de `select_sample`."""
    counts: dict[str, int] = dict.fromkeys(STRATA, 0)
    for cand, _ in entries:
        counts[cand.stratum] = counts.get(cand.stratum, 0) + 1
    parts = [_rubric_header(counts)]
    for idx, (cand, content) in enumerate(entries, start=1):
        shown = content[:input_char_cap]
        trunc = "  _(truncado ao cap)_" if len(content) > input_char_cap else ""
        parts.append(
            f"\n## {idx}. [{cand.stratum}] `{cand.distilled_id}`\n\n"
            f"_item `{cand.item_id}` · criado {cand.created_at}_\n\n"
            f"### Summary destilado\n\n{cand.summary}\n\n"
            f"### Content original (cap {input_char_cap}){trunc}\n\n"
            f"```\n{shown}\n```\n\n"
            "### Veredito\n\n"
            "- Alucinação: [ ] sim  [ ] não\n"
            "- Fidelidade: [ ] aprova [ ] ressalva [ ] reprova\n"
            "- PT-BR natural: [ ] aprova [ ] ressalva [ ] reprova\n"
            "- Entidades: [ ] aprova [ ] ressalva [ ] reprova\n"
            "- Nota: \n"
        )
    return "".join(parts)


# ── Camada de I/O ───────────────────────────────────────────────────────────


def validated_out(path: str) -> Path:
    """Resolve `path` (arg de CLI) e garante que não escapa o diretório de trabalho
    antes de qualquer acesso ao filesystem — barra path traversal por argumento
    malicioso/errado (SonarCloud S8707). Reusado pelo piloto (B2)."""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path.cwd().resolve()):
        raise ValueError(f"caminho de saída fora do diretório de trabalho: {path}")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: lê o acervo, estratifica, grava o doc e imprime só o agregado (não o conteúdo)."""
    parser = argparse.ArgumentParser(description="Amostra de auditoria do dreno (gate B1).")
    parser.add_argument(
        "--out", default=_DEFAULT_OUT, help=f"arquivo de saída (default {_DEFAULT_OUT})"
    )
    parser.add_argument(
        "--cap", type=int, default=_INPUT_CHAR_CAP, help="teto de chars do content mostrado"
    )
    parser.add_argument(
        "--scan-limit", type=int, default=10000, help="quantos destilados varrer do acervo"
    )
    args = parser.parse_args(argv)
    out = validated_out(args.out)  # falha rápido, antes de tocar o banco, se o path escapar

    with client.connect(client.config()) as db:
        rows = knowledge.list_distilled_with_items(db, limit=args.scan_limit)
        sample = select_sample(rows)
        contents = {
            str(i): content
            for i, _title, content in knowledge.items_by_ids(db, [c.item_id for c in sample])
        }

    entries = [(c, contents.get(str(c.item_id), "")) for c in sample]
    doc = render_doc(entries, input_char_cap=args.cap)
    out.write_text(doc, encoding="utf-8")

    counts = {s: sum(1 for c in sample if c.stratum == s) for s in STRATA}
    _log.info("audit.written", out=str(out), **counts)
    got = " ".join(f"{s}={counts[s]}/{_QUOTAS[s]}" for s in STRATA)
    print(f"auditoria escrita em {out} (NÃO commitar) — amostra: {got}")
    if any(counts[s] < _QUOTAS[s] for s in STRATA):
        print("aviso: algum estrato ficou abaixo da cota — a rubrica julga tendência, siga assim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
