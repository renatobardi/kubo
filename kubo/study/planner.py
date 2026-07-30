"""Persona `planner` (ADR-0043, KUBO-136): agrupa capítulos em lições.

Molde de `kubo/workers/finder.py` — classe fina sobre um `Executor`, sem flow e
sem banco. O sumário do material é conteúdo NÃO CONFIÁVEL (o executor demarca e
valida o JSON); a coerência do agrupamento é revalidada AQUI, em código: o LLM
propõe, o sistema confere. Falha de LLM não trava a tela — a rota cai no
`mechanical_proposal`, que é determinístico.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from kubo.executors.base import Executor
from kubo.store.study import MaterialChapter


class PlanLesson(BaseModel):
    """Uma lição proposta: título e os capítulos (por `seq`) que ela cobre."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    chapter_seqs: list[int] = Field(min_length=1, max_length=50)


class PlanProposal(BaseModel):
    """Saída estruturada da persona planner: as lições, na ordem de estudo."""

    model_config = ConfigDict(extra="forbid")

    lessons: list[PlanLesson] = Field(min_length=1, max_length=200)


class Planner:
    """Envolve um `Executor` para propor um plano a partir do sumário do material."""

    def __init__(self, executor: Executor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def propose(self, chapters: Sequence[MaterialChapter]) -> PlanProposal | None:
        """Proposta validada, ou None se o LLM falhar OU devolver plano incoerente.

        Incoerente = `seq` que não existe no material, `seq` repetido entre lições ou
        ordem interna não crescente. A postura é a do Finder: erro vira None e quem
        chama decide o fallback — nunca gravar um plano que não bate com o material.
        """
        raise NotImplementedError


def mechanical_proposal(chapters: Sequence[MaterialChapter]) -> PlanProposal:
    """Fallback determinístico: 1 capítulo = 1 lição, na ordem do `seq`.

    Existe para que a indisponibilidade do LLM atrase a curadoria, não o estudo — a
    UI avisa que a proposta é mecânica e o dono edita.
    """
    raise NotImplementedError
