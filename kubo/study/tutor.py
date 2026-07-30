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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kubo.errors import ExecutorError
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
        instruction = _instruction(
            self._prompt, entry_title=entry_title, work_context=work_context, misses=misses
        )
        try:
            lesson = self._executor.complete(instruction, _content(chapters), LessonOutput)
        except (ExecutorError, ValidationError):
            _log.info("study.tutor.failed", chapters=len(chapters))
            return None
        if not _is_coherent(lesson):
            _log.info("study.tutor.incoherent", questions=len(lesson.quiz))
            return None
        return lesson


def _instruction(prompt: str, *, entry_title: str, work_context: str, misses: Sequence[str]) -> str:
    """Instrução da persona + o que é DADO DO DONO: lição, contexto e erros recentes.

    Nada do texto do material entra aqui: o material vai no `untrusted_content`, senão
    um capítulo com "ignore as instruções acima" ditaria a instrução.
    """
    parts = [prompt, f"Lição de hoje: {entry_title}"]
    if work_context.strip():
        parts.append(f"Contexto de trabalho do aluno: {work_context}")
    if misses:
        parts.append(
            "Questões que o aluno errou recentemente (abra a lição com uma "
            "recapitulação delas):\n" + "\n".join(f"- {miss}" for miss in misses)
        )
    else:
        parts.append("O aluno não errou nada recentemente: NÃO inclua recapitulação.")
    return "\n\n".join(parts)


def _content(chapters: Sequence[MaterialChapter]) -> str:
    """Texto dos capítulos (conteúdo NÃO confiável), com teto de volume.

    O corte é no FIM da string INTEIRA — títulos incluídos — porque o que o teto
    protege é o tamanho do que chega ao provedor, não o de cada pedaço. `_MAX_PROMPT_TEXT`
    é lido do módulo a cada chamada: mexer no teto não exige reconstruir o tutor.
    """
    full = "\n\n".join(f"## {chapter.title}\n{chapter.content}" for chapter in chapters)
    if len(full) <= _MAX_PROMPT_TEXT:
        return full
    # Só o tamanho vai ao log: o conteúdo é material pessoal do dono (CLAUDE.md §Logs).
    _log.warning("study.tutor.content_truncated", chars=len(full), cap=_MAX_PROMPT_TEXT)
    return full[:_MAX_PROMPT_TEXT]


def _is_coherent(lesson: LessonOutput) -> bool:
    """True se toda questão aponta para uma alternativa que existe.

    `answer_index` fora da faixa passa pelo modelo (`ge=0` não sabe quantas opções há)
    e daria ao dono um quiz impossível de acertar — a conferência é em código, como o
    `_is_coherent` do planner.
    """
    return all(item.answer_index < len(item.options) for item in lesson.quiz)
