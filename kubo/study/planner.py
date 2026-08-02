"""Persona `planner` (ADR-0043, KUBO-136): agrupa capítulos em lições.

Molde de `kubo/workers/finder.py` — classe fina sobre um `Executor`, sem flow e
sem banco. O sumário do material é conteúdo NÃO CONFIÁVEL (o executor demarca e
valida o JSON); a coerência do agrupamento é revalidada AQUI, em código: o LLM
propõe, o sistema confere. Falha de LLM não trava a tela — a rota cai no
`mechanical_proposal`, que é determinístico.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kubo.errors import ExecutorError
from kubo.executors.base import Executor, StreamingExecutor
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

    text: str
    lessons: list[PlanLesson] | None = Field(default=None, max_length=200)


class PlannerStreamPlan(BaseModel):
    """Bloco JSON extraído do stream do planner (KUBO-168): só o plano.

    No modo streaming, o texto explicativo viaja fora do bloco JSON (como
    chunks de texto livre). O bloco JSON marcado com ```json contém só as
    lições. `text` não existe aqui — o texto vem dos chunks acumulados.
    """

    model_config = ConfigDict(extra="forbid")

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
        planning_history: Sequence[tuple[str, str]] = (),
    ) -> PlanProposal | None:
        """Proposta validada, ou None se o LLM falhar OU devolver plano incoerente.

        Incoerente = `seq` que não existe no material, `seq` repetido entre lições ou
        ordem interna não crescente. A postura é a do Finder: erro vira None e quem
        chama decide o fallback — nunca gravar um plano que não bate com o material.

        KUBO-164: recebe também campos estruturados (focus, depth), o transcript da
        conversa com `mentor` e os sumários dos materiais — o planner agrupa com base
        na estrutura + contexto, não no conteúdo completo dos capítulos.
        KUBO-165: `planning_history` (conversa da Fase 2) permite repropor com base
        nos ajustes pedidos pelo dono durante o planning.
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
                    planning_history=planning_history,
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
        # lessons=[] ou None = plano não tocado (trata antes de construir PlanProposal,
        # que exige min_length=1 — ValidationError fora do try seria não tratado).
        lessons: list[PlanLesson] | None = update.lessons or None
        if lessons is not None:
            proposal = PlanProposal(lessons=lessons)
            if not _is_coherent(proposal, {chapter.seq for chapter in chapters}):
                _log.info("study.planner.chat_incoherent", chapters=len(chapters))
                lessons = None
        return PlannerChatReply(text=update.text, lessons=lessons)

    def stream_chat(
        self,
        executor: StreamingExecutor,
        *,
        user_message: str,
        chapters: Sequence[MaterialChapter],
        current_plan: Sequence[tuple[str, list[int]]],
        planning_history: Sequence[tuple[str, str]] = (),
        focus: str | None = None,
        depth: str | None = None,
        material_summaries: Sequence[str] = (),
    ) -> Iterator[str]:
        """Chat incremental com streaming (KUBO-168, AC#29).

        Diferente de `chat` (que devolve PlannerChatReply via JSON estruturado),
        `stream_chat` devolve chunks de texto livre. O LLM produz texto
        explicativo seguido de um bloco JSON marcado com ```json. O caller
        acumula os chunks e usa `extract_planner_reply` no final para separar
        texto do plano. Mesma demarcação de `untrusted_content` do `chat`.
        """
        instruction = (
            self._prompt + "\n\nResponda com texto explicativo em português do Brasil. Se a "
            "resposta incluir um plano atualizado, adicione ao final um bloco "
            'JSON marcado com ```json contendo {"lessons": [{"title": "...", '
            '"chapter_seqs": [...]}]}. Se não atualizar o plano, não inclua o '
            "bloco JSON."
        )
        content = _build_chat_content(
            chapters,
            user_message=user_message,
            current_plan=current_plan,
            planning_history=planning_history,
            focus=focus,
            depth=depth,
            material_summaries=material_summaries,
        )
        yield from executor.stream(instruction, content)


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


def _format_history(history: Sequence[tuple[str, str]]) -> str:
    """Formata histórico (role, content) como texto legível para o prompt."""
    return "\n".join(
        f"{'Dono' if role == 'user' else 'Planner'}: {content}" for role, content in history
    )


def _join_with_chapter_block(parts: list[str], chapter_block: str, *, event: str) -> str:
    """Junta parts + chapter_block, truncando o prefixo se necessário (AC8).

    O bloco de capítulos é sempre preservado integralmente — sem ele o planner não
    tem o que agrupar. O prefixo (focus/depth/transcript/histórico/sumários) é
    truncado do fim para caber em `_MAX_SUMMARY_TEXT`.
    """
    full = "\n\n".join(parts)
    if len(full) <= _MAX_SUMMARY_TEXT:
        return full
    budget = _MAX_SUMMARY_TEXT - len(chapter_block) - 4
    prefix = "\n\n".join(parts[:-1])
    if budget > 0:
        _log.warning(f"study.planner.{event}_truncated", chars=len(full), cap=_MAX_SUMMARY_TEXT)
        return prefix[:budget] + "\n\n" + chapter_block
    _log.warning(f"study.planner.{event}_chapters_only", chars=len(chapter_block))
    return chapter_block


def _build_prompt_content(
    chapters: Sequence[MaterialChapter],
    *,
    focus: str | None,
    depth: str | None,
    mentor_transcript: str,
    material_summaries: Sequence[str],
    planning_history: Sequence[tuple[str, str]] = (),
) -> str:
    """Monta o conteúdo NÃO-CONFIÁVEL do prompt do planner (ADR-0047 §2).

    Inclui: campos estruturados (focus, depth), transcript da conversa com `mentor`,
    conversa da Fase 2 com `planner` (KUBO-165), sumários dos materiais e estrutura de
    capítulos (seq, parte, título). O conteúdo completo dos capítulos NÃO vai ao prompt
    — o agrupamento é sobre a estrutura. O bloco de capítulos nunca é truncado (AC8).
    """
    parts: list[str] = []
    if focus:
        parts.append(f"Foco do estudo: {focus}")
    if depth:
        parts.append(f"Profundidade: {depth}")
    if mentor_transcript.strip():
        parts.append(f"Conversa com o mentor:\n{mentor_transcript.strip()}")
    if planning_history:
        parts.append(f"Conversa da Fase 2:\n{_format_history(planning_history)}")
    if material_summaries:
        parts.append("Sumários dos materiais:\n" + "\n".join(material_summaries))
    chapter_block = _chapter_summary(chapters)
    parts.append(chapter_block)
    return _join_with_chapter_block(parts, chapter_block, event="prompt")


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
    plano atual — regenera a proposta inteira, não patcha. A mensagem do dono e o
    bloco de capítulos são sempre preservados (AC8) — o truncamento recai sobre
    plano/histórico/sumários.
    """
    # Mensagem do dono + bloco de capítulos são intocáveis.
    user_block = f"Mensagem do dono: {user_message}"
    chapter_block = _chapter_summary(chapters)
    # Parts truncáveis (plano, histórico, sumários) vão no meio.
    middle: list[str] = []
    if current_plan:
        middle.append(_plan_summary(current_plan))
    if planning_history:
        middle.append(f"Conversa anterior:\n{_format_history(planning_history)}")
    if focus:
        middle.append(f"Foco do estudo: {focus}")
    if depth:
        middle.append(f"Profundidade: {depth}")
    if material_summaries:
        middle.append("Sumários dos materiais:\n" + "\n".join(material_summaries))
    # Monta: user_block + middle (truncável) + chapter_block.
    full_middle = "\n\n".join(middle)
    protected = len(user_block) + len(chapter_block) + 4  # 4 = separadores
    budget = _MAX_SUMMARY_TEXT - protected
    if budget > 0 and len(full_middle) > budget:
        _log.warning(
            "study.planner.chat_prompt_truncated",
            chars=len(full_middle),
            cap=budget,
        )
        full_middle = full_middle[:budget]
    elif budget <= 0:
        _log.warning("study.planner.chat_prompt_minimal", chars=len(chapter_block))
        full_middle = ""
    parts = [user_block]
    if full_middle:
        parts.append(full_middle)
    parts.append(chapter_block)
    return "\n\n".join(parts)


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


# Regex para extrair bloco JSON marcado com ```json ... ``` do texto do stream.
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def extract_planner_reply(
    full_text: str, chapters: Sequence[MaterialChapter]
) -> PlannerChatReply | None:
    """Extrai PlannerChatReply do texto completo acumulado do stream (KUBO-168).

    Separar o texto explicativo do bloco JSON (plano). Se não há bloco JSON,
    devolve só texto (lessons=None). Se o bloco JSON é incoerente (seq
    inexistente), descarta o plano mas preserva o texto — mesma postura do
    `chat`. Remove o bloco JSON do texto exibido ao dono.
    """
    match = _JSON_BLOCK_RE.search(full_text)
    if match is None:
        return PlannerChatReply(text=full_text.strip(), lessons=None)
    json_str = match.group(1)
    text = (full_text[: match.start()] + full_text[match.end() :]).strip()
    try:
        data = json.loads(json_str)
        plan = PlannerStreamPlan.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        _log.info("study.planner.stream_parse_failed")
        return PlannerChatReply(text=full_text.strip(), lessons=None)
    lessons: list[PlanLesson] | None = plan.lessons or None
    if lessons is not None:
        proposal = PlanProposal(lessons=lessons)
        if not _is_coherent(proposal, {chapter.seq for chapter in chapters}):
            _log.info("study.planner.stream_incoherent")
            lessons = None
    return PlannerChatReply(text=text, lessons=lessons)
