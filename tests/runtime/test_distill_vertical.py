"""Vertical de destilação ponta a ponta (integração, SurrealDB) — ADR-0013 §III,
plano 0008 Marco 8.6 Peça 6/6.5.

`run_worker` completo com `DistillerWorker` (executor + embedder FAKE) contra o
banco real: prova o encanamento `items_to_distill` -> executor -> chunk/embed ->
`_persist` resolve ref -> `insert_distilled` com `mentions`. ZERO rede/quota —
`_FakeExecutor`/`_FakeEmbedder` nunca chamam LiteLLM/Gemini (CLAUDE.md: "LLMs em
testes sempre mockados").

O ramo `DistilledPayload` de `kubo.runtime.runner._persist` é HOJE
`raise NotImplementedError` (stub da Peça 6) — estes testes DEVEM falhar por
isso agora (nada persistido, run fecha em erro `worker_exception`); ficam
verdes quando a implementação (GREEN, resolve ref->RecordID + insert_distilled)
entrar.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any, TypeVar, cast

import pytest
from surrealdb import RecordID

from kubo.contracts.models import DistilledPayload, EntityRef, RunResult
from kubo.contracts.worker import RunContext, WorkerManifest
from kubo.errors import MalformedOutputError, RateLimitExhausted
from kubo.runtime.runner import run_worker
from kubo.store import client, migrations
from kubo.store.knowledge import distilled_for, upsert_item, upsert_source
from kubo.workers.distiller import DistillerConfig, DistillerWorker, DistillOutput

pytestmark = pytest.mark.integration

_DISTILL_DB = "test_distill_vertical"
_DIM = 768

T = TypeVar("T")


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_DISTILL_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DISTILL_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DISTILL_DB};")


def _vec(*nonzero: float, dim: int = _DIM) -> list[float]:
    """Vetor de `dim` posições: os valores de `nonzero` nas primeiras posições, resto zero.

    Espelha o helper de tests/store/test_knowledge.py — evita hardcodar 768 floats."""
    values = [float(v) for v in nonzero] + [0.0] * (dim - len(nonzero))
    return values[:dim]


def _count(db: Any, table: str) -> int:
    """Contagem de registros na tabela — nome de tabela é sempre um literal interno do teste."""
    rows: list[dict[str, Any]] = db.query(f"SELECT count() FROM {table} GROUP ALL;")  # noqa: S608
    return int(rows[0]["count"]) if rows else 0


class _FakeExecutor:
    """Fake de `Executor` (ADR-0013 §IV): consome `results` EM ORDEM a cada
    chamada de `complete` — cada elemento é um `DistillOutput` (devolvido) ou
    uma `Exception` (`MalformedOutputError`/`RateLimitExhausted`, levantada).

    ZERO rede: nenhuma chamada real a LiteLLM/provider (CLAUDE.md). Mesmo
    padrão de fila do fake em tests/workers/test_distiller.py, mas indexado
    por lista em vez de dict — mais direto para descrever "1ª chamada, 2ª
    chamada, ..." na ordem em que o worker consome os itens pendentes."""

    def __init__(self, results: Sequence[DistillOutput | Exception]) -> None:
        self._results = list(results)
        self.call_count = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        result = self._results[self.call_count]
        self.call_count += 1
        if isinstance(result, Exception):
            raise result
        return cast(T, result)


class _FakeEmbedder:
    """Fake de `Embedder` (ADR-0013 §I): tripla fixa + vetor determinístico por
    texto, ZERO chamada de rede ao Gemini."""

    model = "gemini-embedding-001"
    dim = _DIM
    task_type = "SEMANTIC_SIMILARITY"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vec(1.0, dim=self.dim) for _ in texts]


class _SyntheticRefWorker:
    """Worker sintético (defensivo, ADR-0013 §III.6): devolve um `DistilledPayload`
    com `ref` BOGUS (9999), nunca atribuído por nenhum `items_to_distill` desta
    run. Este caminho NÃO acontece pelo `DistillerWorker` real — o `ref` sempre
    vem ecoado de um `ItemView` genuíno; existe só para provar que `_persist`
    trata um ref não-resolvível como erro estruturado do run, nunca como crash
    nem como escrita silenciosa no grafo."""

    manifest = WorkerManifest(
        name="synthetic", version="1", config=DistillerConfig, integrations=[]
    )

    def run(self, ctx: RunContext) -> RunResult:
        """Ignora `ctx` por completo — devolve sempre o mesmo payload órfão."""
        payload = DistilledPayload(ref=9999, summary="orfão", entities=[], chunks=[])
        return RunResult(payloads=[payload])


def _run_status(db: Any, run_id: RecordID) -> dict[str, Any]:
    """Lê `status`/`error` do run — leitura repetida nos 4 testes."""
    rows: list[dict[str, Any]] = db.query("SELECT status, error FROM $r;", {"r": run_id})
    return rows[0]


def test_run_worker_distills_pending_items_into_graph(db: Any) -> None:
    """Vertical feliz: 2 itens pendentes -> DistillerWorker (2 saídas do LLM
    fake) -> run_worker persiste 2 `distilled`, cada um com >=1 `chunk`
    embeddado, a entidade citada vira `entity`/`mentions`, `produced_by` liga
    cada distilled ao run, e o run fecha em 'ok'."""
    source = upsert_source(db, kind="rss", canonical="https://x/feed", title="Feed X")
    item_a = upsert_item(
        db, source=source, external_id="a", content="conteúdo bruto A", title="Item A"
    )
    item_b = upsert_item(
        db, source=source, external_id="b", content="conteúdo bruto B", title="Item B"
    )

    executor = _FakeExecutor(
        [
            DistillOutput(summary="resumo A", entities=[EntityRef(name="Anthropic", kind="org")]),
            DistillOutput(summary="resumo B", entities=[]),
        ]
    )

    run_id = run_worker(
        db,
        DistillerWorker(executor),
        config={"max_items": 10},
        embedder=_FakeEmbedder(),
    )

    assert _count(db, "distilled") == 2
    assert _count(db, "chunk") >= 2
    assert distilled_for(db, item_a) != []
    assert distilled_for(db, item_b) != []
    assert _count(db, "entity") == 1
    assert _count(db, "mentions") == 1
    assert _count(db, "produced_by") == 2
    assert _run_status(db, run_id)["status"] == "ok"


def test_run_worker_skips_malformed_item_persists_the_rest(db: Any) -> None:
    """1 item malformado (executor levanta MalformedOutputError) é pulado; o
    outro é destilado normalmente. O run NÃO cai: malformado é contado, não
    fatal (ADR-0013 §III.6) — status fecha 'ok' com 1 `distilled` persistido.

    Qual dos dois itens (a/b) recebe o malformado depende da ORDEM em que a
    store devolve os pendentes (hash do record id, não o external_id) — o
    teste não assume essa correlação; assume só "1 pulado + 1 persistido"."""
    source = upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = upsert_item(db, source=source, external_id="a", content="conteúdo bruto A")
    item_b = upsert_item(db, source=source, external_id="b", content="conteúdo bruto B")

    executor = _FakeExecutor(
        [
            MalformedOutputError("saída não valida contra o schema"),
            DistillOutput(summary="resumo B", entities=[]),
        ]
    )

    run_id = run_worker(
        db,
        DistillerWorker(executor),
        config={"max_items": 10},
        embedder=_FakeEmbedder(),
    )

    assert _count(db, "distilled") == 1
    assert _run_status(db, run_id)["status"] == "ok"
    distilled_a = distilled_for(db, item_a)
    distilled_b = distilled_for(db, item_b)
    # Exatamente um dos dois foi destilado (o outro veio malformado do fake) —
    # comportamento sob teste é "1 pulado, 1 persistido", não QUAL dos dois.
    assert sorted([len(distilled_a), len(distilled_b)]) == [0, 1]


def test_run_worker_rate_limit_returns_partial_and_marks_run_error(db: Any) -> None:
    """3 itens; o 2º estoura RateLimitExhausted -> PARA o loop (o 3º nunca é
    chamado): só o 1º item é persistido, e o run fecha em 'error' com o erro
    kind 'rate_limit_exhausted' (ADR-0013 §V, falha sistêmica, não por-item)."""
    source = upsert_source(db, kind="rss", canonical="https://x/feed")
    upsert_item(db, source=source, external_id="a", content="conteúdo bruto A")
    upsert_item(db, source=source, external_id="b", content="conteúdo bruto B")
    upsert_item(db, source=source, external_id="c", content="conteúdo bruto C")

    executor = _FakeExecutor(
        [
            DistillOutput(summary="resumo primeiro", entities=[]),
            RateLimitExhausted("quota esgotada após N tentativas"),
        ]
    )

    run_id = run_worker(
        db,
        DistillerWorker(executor),
        config={"max_items": 10},
        embedder=_FakeEmbedder(),
    )

    assert _count(db, "distilled") == 1
    row = _run_status(db, run_id)
    assert row["status"] == "error"
    assert row["error"]["kind"] == "rate_limit_exhausted"
    assert executor.call_count == 2  # nunca alcançou o 3º item


def test_run_worker_unresolvable_ref_skips_and_marks_error(db: Any) -> None:
    """Defensivo (§III.6): um payload com `ref` que NUNCA foi atribuído por
    `items_to_distill` (worker sintético, não o DistillerWorker real) não pode
    ser gravado nem crashar o runtime — `_persist` pula o payload órfão e o
    run fecha em 'error' com kind 'unresolvable_ref'; nada é persistido."""
    run_id = run_worker(
        db,
        _SyntheticRefWorker(),
        config={"max_items": 10},
        embedder=_FakeEmbedder(),
    )

    assert _count(db, "distilled") == 0
    row = _run_status(db, run_id)
    assert row["status"] == "error"
    assert row["error"]["kind"] == "unresolvable_ref"
