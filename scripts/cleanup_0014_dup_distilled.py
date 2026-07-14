#!/usr/bin/env python3
"""Limpeza one-off do incidente de duplicação do dreno 0014 (colisão E4).

INCIDENTE: dois `drain_distill` rodaram simultaneamente (dois terminais) → a colisão
E4 prevista no plano: `items_without_distilled` é `ORDER BY id` SEM lock e
`insert_distilled` NÃO é idempotente, então os dois processos pegaram os mesmos itens
e criaram `distilled` duplicados (itens com 2 destilados, 1 chunk cada).

Este script mantém 1 `distilled` por item (o de MENOR id) e apaga os extras + seus
chunks. Retomável (re-seleciona os duplicados restantes), transação por item.

Por que NÃO é helper de store (ADR-0013 §VII): delete de distilled é caminho
INEXISTENTE por decisão de arquitetura — remediação de incidente é one-off (precedente
do backfill/neon_import, D19), não vira API permanente. Validado pelo advisor (Fable 5).

Ordem por item (semântica do SurrealDB v3.1.5, testada pelo advisor): (1) resolve os
chunk-ids do distilled-a-deletar, (2) DELETE dos chunks por id, (3) DELETE do distilled
— o DELETE do NÓ cascadeia as arestas RELATION (`derived_from`/`produced_by`/`mentions`/
`chunk_of`), então NÃO se deleta aresta explicitamente. Chunks ANTES do distilled: se o
distilled for primeiro, o `chunk_of` some na cascata e o chunk vira órfão no índice.

Por default só apaga pares com `summary` IDÊNTICO; par divergente é pulado. Mas o
provider (llama via OpenRouter) é NÃO-DETERMINÍSTICO, então itens duplicados por dreno
concorrente saem com wording diferente (ambos summaries válidos do mesmo item, spot-check
confirmou). `--include-divergent` apaga esses também (mantém o menor id) — é o caso deste
incidente, onde todo par é duplicata verdadeira e manter qualquer um é correto.

Uso (M = `python -m scripts.cleanup_0014_dup_distilled`):
    uv run $M                              # DRY-RUN, só idênticos
    uv run $M --include-divergent          # DRY-RUN, inclui divergentes
    uv run $M --include-divergent --apply  # executa
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

import structlog

from kubo.store import client
from kubo.store.transaction import run_transaction

_log = structlog.get_logger().bind(worker="cleanup_0014_dup")


def find_dup_items(db: Any) -> list[Any]:
    """Itens com mais de 1 `distilled` incoming (duplicados). Read-only."""
    return db.query("SELECT VALUE id FROM item WHERE array::len(<-derived_from) > 1;")


def distilled_of(db: Any, item: Any) -> list[Any]:
    """Todos os `distilled` que derivam de `item`."""
    return db.query("SELECT VALUE <-derived_from<-distilled FROM $i;", {"i": item})[0]


def chunks_of(db: Any, distilled: Any) -> list[Any]:
    """Ids dos `chunk` daquele `distilled` (via `chunk_of`)."""
    return db.query("SELECT VALUE <-chunk_of<-chunk FROM $d;", {"d": distilled})[0]


def summary_of(db: Any, distilled: Any) -> str:
    """Summary de um `distilled` (para o check de identidade do par)."""
    rows = db.query("SELECT VALUE summary FROM $d;", {"d": distilled})
    return rows[0] if rows else ""


def plan_for_item(distilleds: Sequence[Any]) -> tuple[Any, list[Any]]:
    """Dado os distilled de um item, mantém o de MENOR id e devolve `(keep, [delete])`.

    Critério determinístico e verificável (advisor): os pares são equivalentes
    (mesmo modelo, temp=0, mesmo item), então qualquer critério estável serve."""
    ordered = sorted(distilleds, key=str)
    return ordered[0], list(ordered[1:])


def _delete_distilled(db: Any, distilled: Any, chunk_ids: Sequence[Any]) -> None:
    """Apaga os chunks (por id) e o distilled numa transação — a cascata do DELETE do
    nó remove as arestas RELATION. Chunks primeiro (senão `chunk_of` some e orfana)."""
    stmts = [f"DELETE $c{i}" for i in range(len(chunk_ids))] + ["DELETE $d"]
    params: dict[str, Any] = {f"c{i}": c for i, c in enumerate(chunk_ids)}
    params["d"] = distilled
    run_transaction(db, stmts, params)


def _short(rid: Any) -> str:
    """Sufixo curto de um RecordID para o log legível do dry-run."""
    return str(rid).split(":")[-1][:12]


def _count(rows: Any) -> int:
    """Normaliza `SELECT count() ... GROUP ALL` para int — a query volta [] quando 0."""
    return int(rows[0]["count"]) if rows else 0


def main(argv: Sequence[str] | None = None) -> int:
    """DRY-RUN por default (só imprime o plano); `--apply` executa a limpeza."""
    parser = argparse.ArgumentParser(description="Limpeza do incidente de duplicação (0014 E4).")
    parser.add_argument("--apply", action="store_true", help="executa (default é dry-run)")
    parser.add_argument(
        "--include-divergent",
        action="store_true",
        help=(
            "apaga TAMBÉM pares com summary divergente — mesmo item, wording diferente "
            "por não-determinismo do provider; ambos são summaries válidos, mantém o menor id"
        ),
    )
    args = parser.parse_args(argv)

    with client.connect(client.config()) as db:
        items = find_dup_items(db)
        print(f"itens_duplicados: {len(items)}")
        to_delete = skipped_divergent = 0
        for item in items:
            keep, dels = plan_for_item(distilled_of(db, item))
            keep_sum = summary_of(db, keep)
            for d in dels:
                chs = chunks_of(db, d)
                identical = summary_of(db, d) == keep_sum
                delete_it = identical or args.include_divergent
                if identical:
                    flag = "ok"
                elif args.include_divergent:
                    flag = "DIVERGENTE-apaga"
                else:
                    flag = "DIVERGENTE-pula"
                print(
                    f"item {_short(item)} keep={_short(keep)} del={_short(d)} "
                    f"chunks={len(chs)} summary={flag}"
                )
                if not delete_it:
                    skipped_divergent += 1
                    continue
                to_delete += 1
                if args.apply:
                    _delete_distilled(db, d, chs)

        mode = "APLICADO" if args.apply else "DRY-RUN (nada foi deletado)"
        print(
            f"=== {mode}: distilled_a_deletar={to_delete} "
            f"pares_divergentes_pulados={skipped_divergent}"
        )
        if args.apply:
            dup = _count(
                db.query("SELECT count() FROM item WHERE array::len(<-derived_from) > 1 GROUP ALL;")
            )
            orphan = _count(
                db.query("SELECT count() FROM chunk WHERE array::len(->chunk_of) = 0 GROUP ALL;")
            )
            _log.info(
                "cleanup.done",
                deleted=to_delete,
                skipped=skipped_divergent,
                still_dup=dup,
                orphan_chunks=orphan,
            )
            print(
                f"pos-verificacao: chunks_orfaos={orphan} "
                f"itens_ainda_duplicados={dup} (métrica; >0 esperado se houve divergentes pulados)"
            )
            # Falha só em chunk órfão — é a corrupção real do índice vetorial. `dup` pode
            # ser >0 legitimamente (pares divergentes pulados); se >0 sem divergentes, o
            # operador re-roda (retomável).
            if orphan > 0:
                print("FALHA: chunk órfão no índice — investigar antes de retomar o dreno.")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
