"""Camada de acesso ao grafo de conhecimento — a ÚNICA porta para as tabelas §2.3.

Expõe SOMENTE os comportamentos que os consumidores da fase 1 exigem (coleta →
destilação → busca → proveniência). Nenhum método sem teste que o exija.

Idempotência por record ID determinístico derivado da chave natural (UPSERT), o
que elimina a corrida do get-or-create sem SELECT-then-CREATE. Escrita composta
(distilled + chunks + arestas) é atômica via `transaction.run_transaction`.
Conteúdo coletado é hostil: entra sempre por bind param, nunca interpolado.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from surrealdb import RecordID

from kubo.store.transaction import run_transaction

# Parte estática da busca KNN (sem interpolação — a store resolve chunk -> distilled).
_KNN_SELECT = "SELECT id, vector::distance::knn() AS dist, ->chunk_of->distilled AS d FROM chunk"
_MAX_K = 100  # teto de resultados por busca — clamp anti-DoS na borda (escala pessoal, folgado).


@dataclass(frozen=True)
class Chunk:
    """Unidade de embedding a inserir (ADR-0008: um vetor por registro).

    `embedding` já vem gerado pelo chamador — a geração é do M6, não da store.
    `dim` é proveniência (ADR-0006), mantida por registro mesmo sendo o tamanho fixo.
    """

    text: str
    seq: int
    embedding: Sequence[float]
    model: str
    dim: int
    task_type: str


@dataclass(frozen=True)
class SearchHit:
    """Resultado de busca vetorial: o conhecimento (distilled), não o fragmento órfão."""

    distilled: RecordID
    chunk: RecordID
    score: float


def normalize_entity(name: str) -> str:
    """Normaliza nome de entidade para match exato: NFC + casefold + colapso de whitespace.

    Sem fuzzy (decisão vigente): "Python", "  python " e "PYTHON" colapsam no mesmo
    normalizado — logo no mesmo record ID e no mesmo índice UNIQUE.
    """
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


def _rid(table: str, natural_key: str) -> RecordID:
    """Record ID determinístico (sha256 hex da chave natural) — idempotência por id."""
    return RecordID(table, hashlib.sha256(natural_key.encode("utf-8")).hexdigest())


def _fresh(table: str) -> RecordID:
    """Record ID novo para um evento (destilação, execução): cada chamada é distinta."""
    return RecordID(table, secrets.token_hex(16))


def upsert_source(db: Any, *, kind: str, canonical: str, title: str | None = None) -> RecordID:
    """Cria/atualiza uma source pela chave natural (identificador canônico). Idempotente."""
    rid = _rid("source", canonical)
    db.query(
        "UPSERT $r SET kind = $kind, canonical = $canonical, title = $title;",
        {"r": rid, "kind": kind, "canonical": canonical, "title": title},
    )
    return rid


def upsert_item(
    db: Any,
    *,
    source: RecordID,
    external_id: str,
    content: str,
    url: str | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RecordID:
    """Cria/atualiza um item (chave natural: source + external_id, D4) e a aresta
    `item -[from_source]-> source`. Idempotente (2x = no-op): a aresta é reescrita
    (DELETE + RELATE) para não duplicar; tudo numa transação atômica."""
    rid = _rid("item", f"{source}|{external_id}")
    run_transaction(
        db,
        [
            "UPSERT $r SET external_id = $external_id, content = $content, "
            "url = $url, title = $title, metadata = $metadata",
            "DELETE $r->from_source",
            "RELATE $r->from_source->$source",
        ],
        {
            "r": rid,
            "external_id": external_id,
            "content": content,
            "url": url,
            "title": title,
            "metadata": metadata,
            "source": source,
        },
    )
    return rid


def get_or_create_entity(db: Any, *, name: str, kind: str | None = None) -> RecordID:
    """Resolve uma entity pelo nome normalizado, criando se não existir. Idempotente."""
    normalized = normalize_entity(name)
    rid = _rid("entity", normalized)
    db.query(
        "UPSERT $r SET name = $name, normalized = $normalized, kind = $kind;",
        {"r": rid, "name": name, "normalized": normalized, "kind": kind},
    )
    return rid


def insert_distilled(
    db: Any,
    *,
    item: RecordID,
    summary: str,
    chunks: Sequence[Chunk],
    claims: Sequence[str] | None = None,
    run: RecordID | None = None,
    entities: Sequence[RecordID] | None = None,
) -> RecordID:
    """Insere um destilado e tudo que o acompanha numa ÚNICA transação atômica:
    o record `distilled`, `derived_from -> item`, cada `chunk` + `chunk_of ->
    distilled`, `produced_by -> run` (se dado) e `mentions -> entity` (se dados).
    Vetor de dimensão != 768 é rejeitado na borda e reverte a transação inteira."""
    distilled = _fresh("distilled")
    stmts = [
        "CREATE $d SET summary = $summary, claims = $claims",
        "RELATE $d->derived_from->$item",
    ]
    params: dict[str, Any] = {
        "d": distilled,
        "item": item,
        "summary": summary,
        "claims": list(claims) if claims else [],
    }
    for i, ch in enumerate(chunks):
        cid = _rid("chunk", f"{distilled}|{ch.seq}")
        stmts.append(
            f"CREATE $c{i} SET text = $ct{i}, seq = $cs{i}, embedding = $ce{i}, "
            f"model = $cm{i}, dim = $cd{i}, task_type = $ck{i}"
        )
        stmts.append(f"RELATE $c{i}->chunk_of->$d")
        params |= {
            f"c{i}": cid,
            f"ct{i}": ch.text,
            f"cs{i}": ch.seq,
            f"ce{i}": list(ch.embedding),
            f"cm{i}": ch.model,
            f"cd{i}": ch.dim,
            f"ck{i}": ch.task_type,
        }
    if run is not None:
        stmts.append("RELATE $d->produced_by->$run")
        params["run"] = run
    for i, ent in enumerate(entities or []):
        stmts.append(f"RELATE $d->mentions->$e{i}")
        params[f"e{i}"] = ent
    run_transaction(db, stmts, params)
    return distilled


def provenance(db: Any, distilled: RecordID) -> list[RecordID]:
    """Travessia de proveniência distilled -> item -> source (o embrião da prova dos 90 dias)."""
    rows = db.query(
        "SELECT ->derived_from->item->from_source->source AS srcs FROM $d;",
        {"d": distilled},
    )
    return list(rows[0]["srcs"]) if rows else []


def search(db: Any, *, embedding: Sequence[float], k: int) -> list[SearchHit]:
    """Busca vetorial KNN sobre `chunk`, resolvendo cada acerto ao seu `distilled`.

    Função ÚNICA de busca (ADR-0005): sempre injeta EF = max(k*4, 40); nunca aceita
    SurrealQL cru com `<|K|>` de fora. Nenhuma constante deriva do smoke (ADR-0006).
    O operador `<|k,ef|>` exige inteiros literais (não aceita bind) — k/ef são ints
    computados pela store, não conteúdo coletado; o vetor de busca vai por bind param.
    """
    # Clamp na borda: a store é a fronteira em que a spec confia. Um k enorme faria
    # ef = k*4 explodir e degradar o nó HNSW (DoS). Teto de 100 é folgado p/ escala pessoal.
    k = max(1, min(int(k), _MAX_K))
    ef = max(k * 4, 40)
    # k/ef são ints computados pela store (não conteúdo coletado); o vetor vai por bind
    # param. O operador <|k,ef|> exige inteiros literais, não aceita bind (ADR-0005).
    query = f"{_KNN_SELECT} WHERE embedding <|{k},{ef}|> $q ORDER BY dist;"  # noqa: S608
    rows = db.query(query, {"q": list(embedding)})
    return [
        SearchHit(distilled=row["d"][0], chunk=row["id"], score=float(row["dist"])) for row in rows
    ]


def start_run(db: Any, *, worker: str) -> RecordID:
    """Abre um `run` (status 'running', started_at). Retorna o id para finish/fail.

    `stats` fica no default {} do schema até um produtor exigir escrevê-lo (M5+) —
    a store não expõe superfície sem teste que a exija (anti-especulação, plano §3.2).
    """
    rid = _fresh("run")
    db.query("CREATE $r SET worker = $worker, status = 'running';", {"r": rid, "worker": worker})
    return rid


def finish_run(db: Any, run: RecordID) -> None:
    """Fecha um `run` com sucesso (status 'ok', finished_at)."""
    db.query("UPDATE $r SET status = 'ok', finished_at = time::now();", {"r": run})


def fail_run(db: Any, run: RecordID, *, error: dict[str, Any]) -> None:
    """Fecha um `run` com falha (status 'error', finished_at, erro estruturado)."""
    db.query(
        "UPDATE $r SET status = 'error', finished_at = time::now(), error = $error;",
        {"r": run, "error": dict(error)},
    )
