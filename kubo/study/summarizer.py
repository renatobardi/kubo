"""Sumarizador de Material (KUBO-162, ADR-0047 §2): capítulos → sumário.

Gera o sumário síncrono no upload. O sumário é consumido por `mentor` (Fase 1)
e `planner` (Fase 2) — não é o conteúdo completo, é uma destilação. Molde do
`Tutor`: classe fina sobre um `Executor`, sem flow e sem banco.

De que lado cada dado viaja (mesma pilha do Tutor):
- `untrusted_content` leva o texto dos capítulos (nasceu do arquivo enviado);
- a instrução leva só o prompt da persona (do sistema).
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kubo.errors import ExecutorError
from kubo.executors.base import Executor
from kubo.study.parsing import ParsedMaterial

_log = structlog.get_logger(__name__)

# Teto de caracteres do texto dos capítulos que vai ao prompt. Controle de custo:
# um material mal parseado pode trazer um "capítulo" com o livro inteiro.
_MAX_PROMPT_TEXT = 120_000


class SummaryOutput(BaseModel):
    """Saída estruturada do sumarizador: um sumário curto do material."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)


class Summarizer:
    """Envolve um `Executor` para gerar o sumário de um Material no upload."""

    def __init__(self, executor: Executor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def generate(self, parsed: ParsedMaterial) -> str | None:
        """Sumário validado, ou None se o LLM falhar (postura do Tutor/Planner)."""
        content = _content(parsed)
        try:
            output = self._executor.complete(self._prompt, content, SummaryOutput)
        except (ExecutorError, ValidationError):
            _log.info("study.summarizer.failed", chapters=len(parsed.chapters))
            return None
        return output.summary

    def summarize_conversation(self, transcript: str) -> str | None:
        """Resume a conversa do mentor para o planner (KUBO-168, ADR-0047 Emenda 4).

        O transcript cru é longo e polui o prompt do planner. O resumo destila
        a intenção do dono em poucas linhas. Usa o mesmo executor + schema do
        sumário de material, mas com prompt específico de conversa. O
        transcript viaja como `untrusted_content` (entrada hostil — texto
        digitado pelo dono, mas também influenciado pelo mentor que leu o
        material). Se falhar, devolve None — quem chama decide o fallback.
        """
        instruction = (
            "Resuma a conversa entre o dono e o mentor sobre o estudo que ele "
            "está montando. Destaque: o que o dono quer aprender, por que está "
            "estudando, e qualquer contexto de trabalho relevante. Responda em "
            "português do Brasil, em até 500 caracteres."
        )
        try:
            output = self._executor.complete(instruction, transcript, SummaryOutput)
        except (ExecutorError, ValidationError):
            _log.info("study.summarizer.conversation_failed")
            return None
        return output.summary


def _content(parsed: ParsedMaterial) -> str:
    """Monta o `untrusted_content`: título + capítulos truncados ao teto."""
    parts: list[str] = []
    if parsed.title:
        parts.append(f"Título: {parsed.title}")
    total = 0
    for chapter in parsed.chapters:
        remaining = _MAX_PROMPT_TEXT - total
        if remaining <= 0:
            break
        text = chapter.content[:remaining]
        total += len(text)
        parts.append(f"[{chapter.seq}] {chapter.title}\n{text}")
    return "\n\n".join(parts)
