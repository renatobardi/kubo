"""Persona `tutor` (ADR-0043, KUBO-137): capítulos → a lição do dia.

Molde de `kubo/study/planner.py` — classe fina sobre um `Executor`, sem flow e sem
banco. O texto dos capítulos é conteúdo NÃO CONFIÁVEL (vai no `untrusted_content`);
o contexto de trabalho do dono e as questões erradas recentes são dado DELE, e vão na
instrução. A coerência do quiz é revalidada AQUI, em código: o LLM propõe, o sistema
confere.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from pydantic import BaseModel, ConfigDict, Field

from kubo.executors.base import Executor
from kubo.store.study import MaterialChapter

_log = structlog.get_logger(__name__)

# Teto de caracteres do texto dos capítulos que vai ao prompt. Uma lição cobre poucos
# capítulos, mas um material mal parseado pode trazer um "capítulo" com o livro inteiro:
# sem cerca, o custo por lição fica refém do arquivo que o dono enviou.
_MAX_PROMPT_TEXT = 60_000


class QuizItem(BaseModel):
    """Uma questão de múltipla escolha da lição, com a resposta e o porquê dela."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(min_length=2, max_length=4)
    explanation: str = Field(min_length=1, max_length=600)
    answer_index: int = Field(ge=0)


class LessonOutput(BaseModel):
    """Saída estruturada da persona tutor: os blocos da lição + o quiz."""

    model_config = ConfigDict(extra="forbid")

    concept: str = Field(min_length=1, max_length=6000)
    scenario: str = Field(min_length=1, max_length=3000)
    application: str = Field(min_length=1, max_length=3000)
    recap: str | None = Field(default=None, max_length=2000)
    quiz: list[QuizItem] = Field(min_length=2, max_length=3)


class Tutor:
    """Envolve um `Executor` para gerar a lição de uma entrada do plano."""

    def __init__(self, executor: Executor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def generate(
        self,
        *,
        entry_title: str,
        chapters: Sequence[MaterialChapter],
        work_context: str,
        misses: Sequence[str],
    ) -> LessonOutput | None:
        """Lição validada, ou None se o LLM falhar OU devolver um quiz incoerente.

        Incoerente = `answer_index` fora da faixa de `options` em qualquer questão —
        gravar isso daria ao dono um quiz que nunca pode ser acertado. Falha do
        executor também vira None (postura do Finder/Planner): quem chama decide o
        que fazer, e o job da véspera re-tenta na próxima janela.

        `misses` vazio significa lição sem recapitulação: o prompt instrui a omitir o
        bloco, e a pós-validação NÃO exige `recap`.
        """
        raise NotImplementedError
