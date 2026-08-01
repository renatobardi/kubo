"""Testes para kubo/workers/work_context_reviewer.py."""

from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel

from kubo.errors import ExecutorError
from kubo.executors.base import Executor
from kubo.workers.work_context_reviewer import WorkContextReview, WorkContextReviewer

T = TypeVar("T", bound=BaseModel)


class _FakeExecutor(Executor):
    """Executor fake: devolve o `WorkContextReview` preenchido ou levanta exceção."""

    def __init__(
        self, result: WorkContextReview | None = None, exc: Exception | None = None
    ) -> None:
        self._result = result
        self._exc = exc

    def complete(self, _instruction: str, _untrusted_content: str, response_model: type[T]) -> T:
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result  # type: ignore[return-value]


def test_review_returns_revised_text() -> None:
    """`review` devolve o campo `work_context` do output do LLM."""
    executor = _FakeExecutor(result=WorkContextReview(work_context="Arquiteto de dados em escala."))
    reviewer = WorkContextReviewer(executor=executor, prompt="prompt")
    assert reviewer.review("arquiteto dados") == "Arquiteto de dados em escala."


def test_review_strips_whitespace() -> None:
    """Espaços ao redor da resposta são normalizados antes de retornar."""
    executor = _FakeExecutor(result=WorkContextReview(work_context="  Revisado  "))
    reviewer = WorkContextReviewer(executor=executor, prompt="prompt")
    assert reviewer.review("rascunho") == "Revisado"


def test_review_propagates_executor_failure() -> None:
    """Falhas do executor sobem para a rota decidir o status/notice."""
    executor = _FakeExecutor(exc=ExecutorError("boom"))
    reviewer = WorkContextReviewer(executor=executor, prompt="prompt")
    with pytest.raises(ExecutorError):
        reviewer.review("rascunho")
