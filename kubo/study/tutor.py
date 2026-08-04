"""Persona `tutor` (ADR-0043, KUBO-137; ADR-0048, KUBO-189): seções → a lição do dia.

Molde de `kubo/study/planner.py` — classe fina sobre um `Executor`, sem flow e sem
banco. A coerência do quiz é revalidada AQUI, em código: o LLM propõe, o sistema confere.

De que lado cada dado viaja (revisão de segurança da pilha de Estudos):
- `untrusted_content` (a cerca que o `ApiExecutor` demarca e anti-spoofa) leva TUDO que
  nasceu do arquivo enviado: o texto das seções, o TÍTULO DA LIÇÃO (derivado do
  sumário do epub — nada no caminho valida esse texto) e as QUESTÕES ERRADAS recentes
  (enunciados que a persona escreveu lendo o material: promovê-los a instrução deixaria
  o epub dirigir a lição seguinte por dois saltos);
- a instrução leva só o que é do SISTEMA ou digitado pelo DONO: o prompt da persona, o
  contexto de trabalho e a REGRA de recapitulação.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kubo.errors import ExecutorError
from kubo.executors.base import Executor
from kubo.store.study import MaterialSection

_log = structlog.get_logger(__name__)

# Teto de caracteres do texto das seções que vai ao prompt. Uma lição cobre poucas
# seções, mas um material mal parseado pode trazer uma "seção" com o livro inteiro:
# sem cerca, o custo por lição fica refém do arquivo que o dono enviou.
_MAX_PROMPT_TEXT = 60_000


class QuizItem(BaseModel):
    """Uma questão de múltipla escolha da lição, com a resposta e o porquê dela."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(min_length=2, max_length=4)
    explanation: str = Field(min_length=1, max_length=600)
    answer_index: int = Field(ge=0)


class ProvenanceItem(BaseModel):
    """Referência ao trecho do material que originou o conceito da lição.

    O par `(chapter_seq, section_seq)` identifica a seção (visível para o LLM no
    conteúdo como `[(chapter_seq, section_seq)]`), e `quote` é uma citação curta
    do trecho — localizador, não reprodução.
    """

    model_config = ConfigDict(extra="forbid")

    chapter_seq: int = Field(ge=1)
    section_seq: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=300)


class LessonOutput(BaseModel):
    """Saída estruturada da persona tutor: os blocos da lição + o quiz."""

    model_config = ConfigDict(extra="forbid")

    concept: str = Field(min_length=1, max_length=6000)
    scenario: str = Field(min_length=1, max_length=3000)
    application: str = Field(min_length=1, max_length=3000)
    recap: str | None = Field(default=None, max_length=2000)
    provenance: list[ProvenanceItem] = Field(min_length=1, max_length=5)
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
        sections: Sequence[MaterialSection],
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
        instruction = _instruction(self._prompt, work_context=work_context, has_misses=bool(misses))
        content = _content(sections, entry_title=entry_title, misses=misses)
        try:
            lesson = self._executor.complete(instruction, content, LessonOutput)
        except (ExecutorError, ValidationError):
            _log.info("study.tutor.failed", sections=len(sections))
            return None
        if not _is_coherent(lesson):
            _log.info("study.tutor.incoherent", questions=len(lesson.quiz))
            return None
        return lesson


def _instruction(prompt: str, *, work_context: str, has_misses: bool) -> str:
    """Instrução da persona + o que é do SISTEMA ou digitado pelo DONO.

    Nada que tenha nascido do arquivo enviado entra aqui — nem o texto dos capítulos, nem
    o título da lição (que vem do sumário do epub), nem os enunciados errados (escritos
    pela persona LENDO o epub). Tudo isso vai no `untrusted_content`, senão um capítulo
    com "ignore as instruções acima" ditaria a instrução, direto ou por um salto.

    `has_misses` é BOOLEANO de propósito: a instrução carrega a REGRA de recapitulação, e
    o texto dos erros fica do lado de lá da cerca.
    """
    parts = [prompt]
    if work_context.strip():
        parts.append(f"Contexto de trabalho do aluno: {work_context}")
    parts.append(
        "Cada conceito destilado deve trazer proveniência: indique o par "
        "(chapter_seq, section_seq) da seção (visível no conteúdo como "
        "[(chapter_seq, section_seq)]) e uma citação curta (até 300 caracteres) do "
        "trecho que originou o conceito. A citação é localizador, não reprodução."
    )
    if has_misses:
        parts.append(
            "O conteúdo abaixo lista questões que o aluno errou recentemente: abra a "
            "lição com uma recapitulação delas."
        )
    else:
        parts.append("O aluno não errou nada recentemente: NÃO inclua recapitulação.")
    return "\n\n".join(parts)


def _content(
    sections: Sequence[MaterialSection], *, entry_title: str, misses: Sequence[str]
) -> str:
    """Todo o material NÃO confiável do prompt, com teto de volume.

    Ordem deliberada: título e erros recentes primeiro, seções depois. O corte é no
    FIM da string INTEIRA, então o que fica de fora é a cauda do texto das seções — e
    nunca a lista de erros, de que a recapitulação depende.

    O teto vale para o bloco INTEIRO porque o que ele protege é o tamanho do que chega ao
    provedor, não o de cada pedaço. `_MAX_PROMPT_TEXT` é lido do módulo a cada chamada:
    mexer no teto não exige reconstruir o tutor.
    """
    parts = [f"Lição de hoje: {entry_title}"]
    if misses:
        parts.append(
            "Questões que o aluno errou recentemente (para a recapitulação):\n"
            + "\n".join(f"- {miss}" for miss in misses)
        )
    parts.extend(f"## [({s.chapter_seq}, {s.seq})] {s.title}\n{s.content}" for s in sections)
    full = "\n\n".join(parts)
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
