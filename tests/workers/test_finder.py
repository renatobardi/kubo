"""Testes para kubo/workers/finder.py."""

from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel

from kubo.errors import ExecutorError, MalformedOutputError
from kubo.executors.base import Executor
from kubo.workers.finder import Finder

T = TypeVar("T", bound=BaseModel)


class _FakeExecutor(Executor):
    """Executor fake: devolve o response_model preenchido com o JSON configurado."""

    def __init__(self, raw_json: str | None = None, exc: Exception | None = None) -> None:
        self._raw = raw_json
        self._exc = exc

    def complete(self, _instruction: str, _untrusted_content: str, response_model: type[T]) -> T:
        if self._exc is not None:
            raise self._exc
        assert self._raw is not None
        return response_model.model_validate_json(self._raw)


def test_guess_returns_url_when_model_outputs_one() -> None:
    executor = _FakeExecutor(raw_json='{"feed_url": "https://example.com/rss"}')
    finder = Finder(executor=executor, prompt="prompt")
    assert finder.guess("Example") == "https://example.com/rss"


def test_guess_returns_none_on_empty_url() -> None:
    executor = _FakeExecutor(raw_json='{"feed_url": ""}')
    finder = Finder(executor=executor, prompt="prompt")
    assert finder.guess("Example") is None


def test_guess_returns_none_on_whitespace_url() -> None:
    executor = _FakeExecutor(raw_json='{"feed_url": "   "}')
    finder = Finder(executor=executor, prompt="prompt")
    assert finder.guess("Example") is None


@pytest.mark.parametrize("exc", [ExecutorError("boom"), MalformedOutputError("bad")])
def test_guess_returns_none_on_executor_failure(exc: Exception) -> None:
    executor = _FakeExecutor(exc=exc)
    finder = Finder(executor=executor, prompt="prompt")
    assert finder.guess("Example") is None
