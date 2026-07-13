"""Camada pura do dreno one-off (sessão 0014, gate B3).

Testa a decisão de parada (`evaluate_batch`) e a guarda do modelo PINADO — sem
rede, sem SurrealDB. A casca (`drain`/`main`, com run_worker + embedder + store) é
exercida na sessão de execução supervisionada contra o banco vivo (docstring do módulo).
"""

from __future__ import annotations

from scripts import drain_distill as dd


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
