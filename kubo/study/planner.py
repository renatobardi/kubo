"""Persona `planner` (ADR-0043, KUBO-136): agrupa capítulos em lições.

Molde de `kubo/workers/finder.py` — classe fina sobre um `Executor`, sem flow e
sem banco. O sumário do material é conteúdo NÃO CONFIÁVEL (o executor demarca e
valida o JSON); a coerência do agrupamento é revalidada AQUI, em código: o LLM
propõe, o sistema confere. Falha de LLM não trava a tela — a rota cai no
`mechanical_proposal`, que é determinístico.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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


class PlannerChatUpdate(BaseModel):
    """Saída estruturada do chat com planner: texto + plano opcionalmente atualizado.

    `lessons` é None quando a mensagem não toca o plano (só conversa). Quando presente,
    é a nova proposta INTEIRA — o planner regenera, não patcha.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=0)
    lessons: list[PlanLesson] | None = Field(default=None, max_length=200)


@dataclass(frozen=True)
class PlannerChatReply:
    """Resposta do planner no chat: texto + plano atualizado (ou None se não tocou)."""

    text: str
    lessons: list[PlanLesson] | None = None


class Planner:
    """Envolve um `Executor` para propor um plano a partir do sumário do material."""

    def __init__(self, executor: Executor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def propose(
        self,
        chapters: Sequence[MaterialChapter],
        *,
        focus: str | None = None,
        depth: str | None = None,
        mentor_transcript: str = "",
        material_summaries: Sequence[str] = (),
    ) -> PlanProposal | None:
        """Proposta validada, ou None se o LLM falhar OU devolver plano incoerente.

        Incoerente = `seq` que não existe no material, `seq` repetido entre lições ou
        ordem interna não crescente. A postura é a do Finder: erro vira None e quem
        chama decide o fallback — nunca gravar um plano que não bate com o material.

        KUBO-164: recebe também campos estruturados (focus, depth), o transcript da
        conversa com `mentor` e os sumários dos materiais — o planner agrupa com base
        na estrutura + contexto, não no conteúdo completo dos capítulos.
        """
        try:
            proposal = self._executor.complete(
                self._prompt,
                _build_prompt_content(
                    chapters,
                    focus=focus,
                    depth=depth,
                    mentor_transcript=mentor_transcript,
                    material_summaries=material_summaries,
                ),
                PlanProposal,
            )
        except (ExecutorError, ValidationError):
            _log.info("study.planner.failed", chapters=len(chapters))
            return None
        if not _is_coherent(proposal, {chapter.seq for chapter in chapters}):
            _log.info("study.planner.incoherent", chapters=len(chapters))
            return None
        return proposal

    def chat(
        self,
        *,
        user_message: str,
        chapters: Sequence[MaterialChapter],
        current_plan: Sequence[tuple[str, list[int]]],
        planning_history: Sequence[tuple[str, str]] = (),
        focus: str | None = None,
        depth: str | None = None,
        material_summaries: Sequence[str] = (),
    ) -> PlannerChatReply | None:
        """Chat incremental com planner (Fase 2, KUBO-165).

        Devolve texto + plano atualizado (ou None se o LLM falhar). O plano atual chega
        ao prompt para ajuste incremental — o planner regenera, não patcha. Se o LLM
        devolver plano incoerente, o texto é preservado e `lessons` vira None: o dono
        não perde a resposta só porque o plano veio ruim.
        """
        try:
            update = self._executor.complete(
                self._prompt,
                _build_chat_content(
                    chapters,
                    user_message=user_message,
                    current_plan=current_plan,
                    planning_history=planning_history,
                    focus=focus,
                    depth=depth,
                    material_summaries=material_summaries,
                ),
                PlannerChatUpdate,
            )
        except (ExecutorError, ValidationError):
            _log.info("study.planner.chat_failed", chapters=len(chapters))
            return None
        lessons: list[PlanLesson] | None = update.lessons
        if lessons is not None:
            proposal = PlanProposal(lessons=lessons)
            if not _is_coherent(proposal, {chapter.seq for chapter in chapters}):
                _log.info("study.planner.chat_incoherent", chapters=len(chapters))
                lessons = None
        return PlannerChatReply(text=update.text, lessons=lessons)


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


def _build_prompt_content(
    chapters: Sequence[MaterialChapter],
    *,
    focus: str | None,
    depth: str | None,
    mentor_transcript: str,
    material_summaries: Sequence[str],
) -> str:
    """Monta o conteúdo NÃO-CONFIÁVEL do prompt do planner (ADR-0047 §2).

    Inclui: campos estruturados (focus, depth), transcript da conversa com `mentor`,
    sumários dos materiais e estrutura de capítulos (seq, parte, título). O conteúdo
    completo dos capítulos NÃO vai ao prompt — o agrupamento é sobre a estrutura.
    """
    parts: list[str] = []
    if focus:
        parts.append(f"Foco do estudo: {focus}")
    if depth:
        parts.append(f"Profundidade: {depth}")
    if mentor_transcript.strip():
        parts.append(f"Conversa com o mentor:\n{mentor_transcript.strip()}")
    if material_summaries:
        parts.append("Sumários dos materiais:\n" + "\n".join(material_summaries))
    parts.append(_chapter_summary(chapters))
    full = "\n\n".join(parts)
    if len(full) <= _MAX_SUMMARY_TEXT:
        return full
    _log.warning("study.planner.prompt_truncated", chars=len(full), cap=_MAX_SUMMARY_TEXT)
    return full[:_MAX_SUMMARY_TEXT]


def _chapter_summary(chapters: Sequence[MaterialChapter]) -> str:
    """Sumário da estrutura de capítulos (seq, parte, título)."""
    return "\n".join(
        f"{chapter.seq}. {chapter.title}" + (f" [{chapter.part}]" if chapter.part else "")
        for chapter in _in_reading_order(chapters)
    )


def _plan_summary(current_plan: Sequence[tuple[str, list[int]]]) -> str:
    """Plano atual como texto legível para o prompt do planner."""
    lines = [
        f"{i + 1}. {title} (capítulos: {', '.join(map(str, seqs))})"
        for i, (title, seqs) in enumerate(current_plan)
    ]
    return "Plano atual:\n" + "\n".join(lines)


def _build_chat_content(
    chapters: Sequence[MaterialChapter],
    *,
    user_message: str,
    current_plan: Sequence[tuple[str, list[int]]],
    planning_history: Sequence[tuple[str, str]],
    focus: str | None,
    depth: str | None,
    material_summaries: Sequence[str],
) -> str:
    """Conteúdo NÃO-CONFIÁVEL do prompt de chat com planner (KUBO-165).

    Inclui: mensagem do dono, plano atual, histórico da conversa, campos estruturados,
    sumários e estrutura de capítulos. O planner ajusta incrementalmente com base no
    plano atual — regenera a proposta inteira, não patcha.
    """
    parts: list[str] = [f"Mensagem do dono: {user_message}"]
    if current_plan:
        parts.append(_plan_summary(current_plan))
    if planning_history:
        history_text = "\n".join(
            f"{'Dono' if role == 'user' else 'Planner'}: {content}"
            for role, content in planning_history
        )
        parts.append(f"Conversa anterior:\n{history_text}")
    if focus:
        parts.append(f"Foco do estudo: {focus}")
    if depth:
        parts.append(f"Profundidade: {depth}")
    if material_summaries:
        parts.append("Sumários dos materiais:\n" + "\n".join(material_summaries))
    parts.append(_chapter_summary(chapters))
    full = "\n\n".join(parts)
    if len(full) <= _MAX_SUMMARY_TEXT:
        return full
    _log.warning("study.planner.chat_prompt_truncated", chars=len(full), cap=_MAX_SUMMARY_TEXT)
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
