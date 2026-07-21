"""Finder — chute de URL de feed a partir de um nome (KUBO-56).

Síncrono, sem flow/task; usado por `POST /sources/test`. A persona vê apenas o
 texto digitado pelo dono e devolve uma sugestão, que a rota SEMPRE valida com
fetch real antes de usar."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

from kubo.errors import ExecutorError, MalformedOutputError
from kubo.executors.base import Executor


class FinderGuess(BaseModel):
    """Saída estruturada da persona finder: uma URL de feed (vazia = não sei)."""

    model_config = ConfigDict(extra="forbid")

    feed_url: str = ""


class Finder:
    """Envolve um Executor para chutar `feed_url` a partir do nome de uma empresa."""

    def __init__(self, executor: Executor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def guess(self, name: str) -> str | None:
        """Devolve a URL chutada ou None se o LLM falhar, malformar ou devolver vazio."""
        try:
            result = self._executor.complete(self._prompt, name, FinderGuess)
        except (ExecutorError, MalformedOutputError, ValidationError):
            return None
        url = result.feed_url.strip()
        return url or None
