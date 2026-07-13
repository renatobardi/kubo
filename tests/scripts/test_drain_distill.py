"""Camada pura do dreno one-off (sessão 0014, gate B3).

Testa a decisão de parada (`evaluate_batch`) e a guarda do modelo PINADO — sem
rede, sem SurrealDB. A casca (`drain`/`main`, com run_worker + embedder + store) é
exercida na sessão de execução supervisionada contra o banco vivo (docstring do módulo).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel

from kubo.contracts.models import EntityRef
from kubo.store import knowledge
from kubo.workers.distiller import DistillerWorker, DistillOutput
from scripts import drain_distill as dd

T = TypeVar("T", bound=BaseModel)


def test_evaluate_batch_continues_while_progressing() -> None:
    """Batch drenou itens, sobrou pendente e sem erro: CONTINUA."""
    outcome = dd.evaluate_batch("ok", pending_before=100, pending_after=75)
    assert outcome.distilled == 25
    assert outcome.stop is False
    assert outcome.reason == ""


def test_evaluate_batch_stops_done_when_backlog_empty() -> None:
    """Pendentes zeraram: PARA com 'done'."""
    outcome = dd.evaluate_batch("ok", pending_before=25, pending_after=0)
    assert outcome.stop is True
    assert outcome.reason == "done"


def test_evaluate_batch_stops_error_on_systemic_failure() -> None:
    """run em 'error' (rate_limit_day/embedding_failed) PARA com 'error' — retentar no
    mesmo dia não recupera a quota; o parcial já foi persistido pelo worker."""
    outcome = dd.evaluate_batch("error", pending_before=100, pending_after=90)
    assert outcome.distilled == 10  # o parcial conta
    assert outcome.stop is True
    assert outcome.reason == "error"


def test_evaluate_batch_stops_stuck_on_zero_progress() -> None:
    """Sem progresso (só malformados/vazios no batch) com pendentes restantes: PARA
    'stuck' — não queima dinheiro re-tentando os mesmos itens que não drenam."""
    outcome = dd.evaluate_batch("ok", pending_before=50, pending_after=50)
    assert outcome.stop is True
    assert outcome.reason == "stuck"


def test_drain_model_is_paid_never_groq_free() -> None:
    """O modelo do dreno é PAGO por construção (D35): nunca aponta para um modelo
    `groq/` (o free tier diário é preservado; a conta Groq não sofre upgrade)."""
    assert not dd._DRAIN_MODEL.startswith("groq/")


class _FakeExecutor:
    """Fake de Executor: devolve DistillOutput em ordem, ZERO rede (CLAUDE.md)."""

    def __init__(self, outputs: Sequence[DistillOutput]) -> None:
        self._outputs = list(outputs)
        self.calls = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        out = self._outputs[self.calls]
        self.calls += 1
        return cast(T, out)


class _FakeEmbedder:
    """Fake de Embedder: tripla fixa + vetor determinístico, ZERO rede ao Gemini."""

    model = "gemini-embedding-001"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 767 for _ in texts]


@pytest.mark.integration
def test_drain_distills_backlog_and_reconciles(db, monkeypatch) -> None:
    """Casca de drain(): com _build_worker e GeminiEmbedder.from_env mockados (ZERO rede/
    quota paga), drena 2 itens pendentes do banco real via run_worker e reconcilia
    (inicial 2 → final 0, drenados 2, motivo 'done'). Fecha o risco de wiring que só
    apareceria em execução real e gastaria API paga (achado CodeRabbit)."""
    src = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    knowledge.upsert_item(db, source=src, external_id="a", content="conteúdo A sobre a Anthropic")
    knowledge.upsert_item(db, source=src, external_id="b", content="conteúdo B sobre a Anthropic")

    executor = _FakeExecutor(
        [
            DistillOutput(summary="resumo A", entities=[EntityRef(name="Anthropic", kind="org")]),
            DistillOutput(summary="resumo B", entities=[]),
        ]
    )
    monkeypatch.setattr(dd, "_build_worker", lambda: DistillerWorker(executor))
    monkeypatch.setattr(dd.GeminiEmbedder, "from_env", staticmethod(lambda: _FakeEmbedder()))

    initial, final, drained, reason = dd.drain(
        db, batch_size=10, max_batches=3, delay=0.0, sleep=lambda _: None
    )

    assert initial == 2
    assert final == 0
    assert drained == 2
    assert reason == "done"
    assert knowledge.count_items_without_distilled(db) == 0
