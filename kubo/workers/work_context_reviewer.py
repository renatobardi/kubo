"""Revisor de contexto de trabalho (KUBO-152).

Síncrono, sem flow/task; usado por `POST /profile/work-context/review`. A persona
recebe o rascunho digitado pelo dono e devolve uma versão revisada, que a rota
renderiza no textarea sem persistir.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from kubo.executors.base import Executor


class WorkContextReview(BaseModel):
    """Saída estruturada do revisor: o contexto de trabalho revisado."""

    model_config = ConfigDict(extra="forbid")

    work_context: str


class WorkContextReviewer:
    """Envolve um Executor para revisar o contexto de trabalho do dono."""

    def __init__(self, executor: Executor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def review(self, draft: str) -> str:
        """Devolve o texto revisado, validado contra o schema."""
        result = self._executor.complete(self._prompt, draft, WorkContextReview)
        return result.work_context.strip()
