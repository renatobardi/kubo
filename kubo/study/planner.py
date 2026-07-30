"""Persona `planner` (ADR-0043, KUBO-136): agrupa capítulos em lições.

Molde de `kubo/workers/finder.py` — classe fina sobre um `Executor`, sem flow e
sem banco. O sumário do material é conteúdo NÃO CONFIÁVEL (o executor demarca e
valida o JSON); a coerência do agrupamento é revalidada AQUI, em código: o LLM
propõe, o sistema confere. Falha de LLM não trava a tela — a rota cai no
`mechanical_proposal`, que é determinístico.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kubo.errors import ExecutorError
from kubo.executors.base import Executor
from kubo.store.study import MaterialChapter

_log = structlog.get_logger(__name__)

# Teto de `title` do modelo — o título vem do sumário do arquivo (entrada hostil).
_MAX_TITLE = 200

# Teto de caracteres do SUMÁRIO que vai ao prompt. Menor que o teto do tutor (60k) porque
# aqui só viajam títulos: 20k já comportam ~200 capítulos (o máximo que a rota aceita para
# propor) com folga. A cerca existe porque o sumário é o único prompt de Estudos montado a
# partir de um número ARBITRÁRIO de pedaços do arquivo enviado — um epub com milhares de
# "capítulos", ou um título com o livro inteiro dentro, faria o custo da proposta refém do
# upload. O corte é no fim: o começo do sumário é o que o LLM precisa para agrupar.
_MAX_SUMMARY_TEXT = 20_000


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
        try:
            proposal = self._executor.complete(self._prompt, _summary(chapters), PlanProposal)
        except (ExecutorError, ValidationError):
            _log.info("study.planner.failed", chapters=len(chapters))
            return None
        if not _is_coherent(proposal, {chapter.seq for chapter in chapters}):
            _log.info("study.planner.incoherent", chapters=len(chapters))
            return None
        return proposal


def mechanical_proposal(chapters: Sequence[MaterialChapter]) -> PlanProposal:
    """Fallback determinístico: 1 capítulo = 1 lição, na ordem do `seq`.

    Existe para que a indisponibilidade do LLM atrase a curadoria, não o estudo — a
    UI avisa que a proposta é mecânica e o dono edita.
    """
    return PlanProposal(
        lessons=[
            PlanLesson(title=_lesson_title(chapter), chapter_seqs=[chapter.seq])
            for chapter in _in_reading_order(chapters)
        ]
    )


def _in_reading_order(chapters: Sequence[MaterialChapter]) -> list[MaterialChapter]:
    """Capítulos na ordem do `seq` — a ordem do livro, não a da lista recebida."""
    return sorted(chapters, key=lambda chapter: chapter.seq)


def _lesson_title(chapter: MaterialChapter) -> str:
    """Título de lição derivado do capítulo, dentro dos limites do modelo.

    O título vem do sumário do arquivo enviado (entrada hostil): vazio ou longo demais
    quebraria a validação do `PlanLesson` e derrubaria a tela em vez do fallback.
    """
    return chapter.title.strip()[:_MAX_TITLE] or f"Capítulo {chapter.seq}"


def _summary(chapters: Sequence[MaterialChapter]) -> str:
    """Sumário do material (seq, parte, título) — o conteúdo NÃO-CONFIÁVEL do prompt.

    Só o sumário: o texto dos capítulos não cabe no orçamento de tokens e não é
    necessário para agrupar — o agrupamento é sobre a estrutura, não sobre o conteúdo.

    Com teto de volume (`_MAX_SUMMARY_TEXT`, lido do módulo a cada chamada): títulos vêm
    do arquivo enviado e nada limita o tamanho deles antes daqui. Truncar degrada a
    proposta (o dono edita, e o fallback mecânico continua de pé); não truncar deixaria
    o custo do prompt nas mãos de quem monta o epub.
    """
    full = "\n".join(
        f"{chapter.seq}. {chapter.title}" + (f" [{chapter.part}]" if chapter.part else "")
        for chapter in _in_reading_order(chapters)
    )
    if len(full) <= _MAX_SUMMARY_TEXT:
        return full
    # Só o tamanho vai ao log: títulos são conteúdo do material pessoal (CLAUDE.md §Logs).
    _log.warning("study.planner.summary_truncated", chars=len(full), cap=_MAX_SUMMARY_TEXT)
    return full[:_MAX_SUMMARY_TEXT]


def _is_coherent(proposal: PlanProposal, known: set[int]) -> bool:
    """True se a proposta bate com o material: `seq` existente, único e em ordem.

    A checagem é sobre a proposta INTEIRA — uma lição incoerente invalida tudo, porque
    salvar o resto daria ao dono um plano que não cobre o livro que ele mandou estudar.
    """
    seen: set[int] = set()
    for lesson in proposal.lessons:
        seqs = lesson.chapter_seqs
        if any(seq not in known for seq in seqs):
            return False
        if any(a >= b for a, b in zip(seqs, seqs[1:], strict=False)):
            return False
        if seen & set(seqs):
            return False
        seen |= set(seqs)
    return True
