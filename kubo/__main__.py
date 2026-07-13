"""CLI `kubo` — busca vetorial (`query`) e proveniência (`show`) sobre o grafo
de conhecimento (ADR-0013 §8.5, Marco 8.5). Entrypoint `python -m kubo`; o
`console_scripts` (comando `kubo` nu) entra pelo `[project.scripts]` do
pyproject, que aponta para `main` abaixo.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from surrealdb import RecordID

from kubo.distribution.destinations import (
    load_destinations,
    resolve_base_url,
    resolve_destinations,
)
from kubo.embedding import Embedder, GeminiEmbedder
from kubo.errors import ConfigError, EmbeddingError
from kubo.runtime.flow_runner import FlowRunResult, run_flow
from kubo.store import client, knowledge
from kubo.store.knowledge import DistilledView, SearchHit

_DESTINATIONS_PATH = Path(__file__).parents[1] / "destinations.yaml"

_DISTILLED_TABLE = "distilled"


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _sanitize(text: str) -> str:
    """Remove caracteres de controle (ESC/OSC etc.), preservando `\\n` e `\\t`.

    `text` vem de summaries/claims/títulos derivados de conteúdo coletado
    HOSTIL (§VIII) — sem isto, um item malicioso poderia injetar sequências
    de escape de terminal (ESC/OSC) na saída de `kubo query`/`kubo show`.
    Não use para as mensagens de erro da própria CLI (essas são nossas).
    """
    return _CONTROL_CHARS_RE.sub("", text)


def parse_distilled_id(raw: str) -> RecordID:
    """Resolve o id de um `distilled` a partir de entrada de argv (hostil por padrão).

    Aceita `"distilled:<key>"` ou `"<key>"` (sem prefixo assume a tabela
    `distilled`). A tabela do RecordID resultante é SEMPRE `distilled` — nunca
    deixa o argv escolher a tabela (`"item:x"`/`"source:x"` levantam
    `ValueError`), senão `kubo show` viraria uma porta para ler qualquer
    registro do grafo, não só destilados. String vazia/whitespace também é
    `ValueError`.
    """
    key = raw.strip()
    if not key:
        raise ValueError("id vazio")
    if ":" in key:
        table, _, key = key.partition(":")
        if table != _DISTILLED_TABLE:
            raise ValueError(f"id deve ser da tabela distilled, veio {table!r}")
        if not key:
            raise ValueError("id vazio")
    return RecordID(_DISTILLED_TABLE, key)


def dedupe_hits(hits: list[SearchHit]) -> list[SearchHit]:
    """Colapsa hits por CHUNK a um hit por `distilled`.

    `search` devolve um `SearchHit` por chunk; dois chunks do mesmo `distilled`
    duplicariam o mesmo summary na saída de `kubo query`. Mantém o hit de MENOR
    `score` (distância — mais perto) por distilled, resultado ordenado por
    score ascendente.
    """
    # RecordID não é hashável (SDK 2.0.0) — chave do dedupe é a forma string do id.
    best: dict[str, SearchHit] = {}
    for hit in hits:
        key = str(hit.distilled)
        current = best.get(key)
        if current is None or hit.score < current.score:
            best[key] = hit
    return sorted(best.values(), key=lambda h: h.score)


def format_query_results(results: list[tuple[SearchHit, DistilledView]]) -> str:
    """Bloco textual de `kubo query`.

    Para cada resultado: o id do distilled (insumo direto de `kubo show` — sem
    ele os dois comandos não se compõem), a distância (rotulada como distância/
    proximidade, nunca "score" solto e ambíguo) e o summary.
    """
    blocks = [
        f"[{i}] {view.id}  (distância {hit.score:.4f})\n    {view.summary}"
        for i, (hit, view) in enumerate(results, start=1)
    ]
    return "\n".join(blocks)


def format_distilled(view: DistilledView, *, provenance: bool) -> str:
    """Bloco textual de `kubo show`.

    Sem `provenance`: summary + claims. Com `provenance=True`, soma a cadeia
    item -> source (url/title/canonical) e os runs (worker) que produziram o
    destilado.
    """
    lines = [view.summary]
    if view.claims:
        lines.append("")
        lines.extend(f"- {claim}" for claim in view.claims)
    if provenance:
        lines.append("")
        lines.append("Proveniência:")
        for item in view.items:
            source_label = item.source_title or item.source_canonical
            lines.append(f"  fonte: {source_label} ({item.source_canonical})")
            item_label = item.title or item.external_id
            lines.append(f"  item: {item_label}")
            if item.url:
                lines.append(f"    url: {item.url}")
        for run in view.runs:
            lines.append(f"  produzido por: {run.worker}")
    return "\n".join(lines)


def run_query(db: Any, embedder: Embedder, question: str, *, k: int) -> str:
    """Orquestra `kubo query`: embedda a pergunta, busca (`search`), deduplica
    por distilled (`dedupe_hits`), resolve a proveniência de cada hit
    (`read_distilled`) e formata (`format_query_results`).
    """
    vector = embedder.embed([question])[0]
    hits = dedupe_hits(knowledge.search(db, embedding=vector, k=k))
    results: list[tuple[SearchHit, DistilledView]] = []
    for hit in hits:
        view = knowledge.read_distilled(db, hit.distilled)
        if view is None:
            # defensivo: hit aponta para um distilled que sumiu entre a busca e a
            # leitura — pula em vez de quebrar o comando inteiro.
            continue
        results.append((hit, view))
    return format_query_results(results)


def run_show(db: Any, raw_id: str, *, provenance: bool) -> str | None:
    """Orquestra `kubo show`: resolve o id (`parse_distilled_id`), lê o
    distilled (`read_distilled`) e formata (`format_distilled`); `None` se o
    distilled não existe no grafo.
    """
    distilled = parse_distilled_id(raw_id)
    view = knowledge.read_distilled(db, distilled)
    if view is None:
        return None
    return format_distilled(view, provenance=provenance)


def run_flow_command(
    db: Any, *, template: str, question: str, destination_id: str
) -> FlowRunResult:
    """Resolve as dependências do flow (embedder Gemini, destino do destinations.yaml, base
    URL — tudo por env, invariante 8) e executa o flow SÍNCRONO no processo do CLI (ADR-0016
    §VII). Destino inexistente falha alto (ConfigError). O flow runner faz o resto."""
    destinations = resolve_destinations(load_destinations(_DESTINATIONS_PATH))
    dest = next((d for d in destinations if d.id == destination_id), None)
    if dest is None:
        raise ConfigError(f"destino '{destination_id}' não existe em destinations.yaml")
    embedder = GeminiEmbedder.from_env()
    return run_flow(
        db,
        template_name=template,
        question=question,
        embedder=embedder,
        destination=dest,
        base_url=resolve_base_url(),
    )


def _handle_flow(db: Any, args: argparse.Namespace) -> int:
    """Despacha `kubo flow run <template> "pergunta"`: executa e imprime o resultado. Exit 0
    se entregue, 1 se o flow terminou em `failed` (o deliverable pode existir mesmo assim)."""
    if args.flow_command != "run":
        print('uso: kubo flow run <template> "pergunta"', file=sys.stderr)
        return 2
    result = run_flow_command(
        db, template=args.template, question=args.question, destination_id=args.destination
    )
    print(f"flow {result.flow} — {result.state} (run {result.run})")
    return 0 if result.state == "delivered" else 1


def _build_parser() -> argparse.ArgumentParser:
    """Monta o parser com os subcomandos `query`, `show` e `flow run`."""
    parser = argparse.ArgumentParser(prog="kubo")
    subparsers = parser.add_subparsers(dest="command")

    query_parser = subparsers.add_parser("query", help="busca vetorial no grafo destilado")
    query_parser.add_argument("question")
    query_parser.add_argument("--k", type=int, default=5)

    show_parser = subparsers.add_parser("show", help="mostra um distilled por id")
    show_parser.add_argument("id")
    show_parser.add_argument("--provenance", action="store_true")

    flow_parser = subparsers.add_parser("flow", help="instancia e executa flows")
    flow_sub = flow_parser.add_subparsers(dest="flow_command")
    run_parser = flow_sub.add_parser("run", help="flow run <template> <pergunta>")
    run_parser.add_argument("template")
    run_parser.add_argument("question")
    run_parser.add_argument("--destination", default="owner-telegram")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint da CLI: subcomandos `query "pergunta"` e `show <id> [--provenance]`."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        # ponytail: catch de fronteira do CLI — imprime e retorna, nunca traceback;
        # não é except-pass.
        with client.connect() as db:
            if args.command == "query":
                embedder = GeminiEmbedder.from_env()
                out = run_query(db, embedder, args.question, k=args.k)
                if not out.strip():
                    print("nenhum resultado.")
                else:
                    print(_sanitize(out))
                return 0

            if args.command == "flow":
                return _handle_flow(db, args)

            try:
                out_show = run_show(db, args.id, provenance=args.provenance)
            except ValueError as exc:
                print(f"id inválido: {exc}", file=sys.stderr)
                return 1
            if out_show is None:
                print(f"distilled não encontrado: {args.id}", file=sys.stderr)
                return 1
            print(_sanitize(out_show))
            return 0
    except ConfigError as exc:
        print(f"erro de configuração: {exc}", file=sys.stderr)
        return 2
    except EmbeddingError as exc:
        # Falha da API de embedding em runtime (ex.: 500/429 do Gemini ao embeddar a
        # pergunta) — erro de execução, mensagem limpa e exit 1, nunca traceback.
        print(f"falha ao gerar embedding da pergunta: {exc}", file=sys.stderr)
        return 1
    except ConnectionError as exc:
        print(f"não foi possível conectar ao banco: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
