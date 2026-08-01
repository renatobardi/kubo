"""Persona `mentor` (ADR-0047 §6, KUBO-163): chat conversacional na Fase 1.

Molde do `Tutor`/`Planner`: classe fina sobre um `StreamingExecutor`, sem flow
e sem banco. A diferença é o streaming: o mentor devolve a resposta em chunks
(surgindo aos poucos), não um objeto estruturado de uma vez.

De que lado cada dado viaja (mesma pilha do Tutor):
- `untrusted_content` leva os sumários dos Materiais + histórico da conversa
  (nasceu do arquivo enviado e das mensagens anteriores — tudo não-confiável);
- a instrução leva só o que é do SISTEMA ou digitado pelo DONO: o prompt da
  persona e o `work_context` do perfil do usuário.

Sugestões (nome, foco, profundidade) são extraídas da resposta por parsing de
marcações em colchetes (`[Sugestão: ...]`, `[Foco: ...]`, `[Profundidade: ...]`),
não por LLM estruturado — o chat é texto livre, as marcações são convenção
do prompt. A extração é em código (não confia no LLM para produzir JSON válido
no meio de texto conversacional).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import structlog

from kubo.executors.base import StreamingExecutor

_log = structlog.get_logger(__name__)

# Valores válidos de profundidade (espelha o ASSERT da migration 0031).
_VALID_DEPTH = ("superficial", "intermediario", "aprofundado")

# Teto de caracteres do histórico que vai ao prompt (janela deslizante).
# Os turnos mais antigos são truncados primeiro — os recentes ficam inteiros.
_MAX_HISTORY_CHARS = 20_000

# Padrões de extração das marcações de sugestão.
_NAME_RE = re.compile(r"\[Sugestão:\s*(.+?)\]")
_FOCUS_RE = re.compile(r"\[Foco:\s*(.+?)\]")
_DEPTH_RE = re.compile(r"\[Profundidade:\s*(.+?)\]")
# Padrão para limpar TODAS as marcações do texto exibido ao dono.
_ALL_SUGGESTION_RE = re.compile(r"\[(?:Sugestão|Foco|Profundidade):\s*.+?\]")


@dataclass(frozen=True)
class MentorReply:
    """Resposta do mentor: texto do chat + sugestões extraídas (ou None)."""

    text: str
    suggested_name: str | None = None
    suggested_focus: str | None = None
    suggested_depth: str | None = None


class Mentor:
    """Envolve um `StreamingExecutor` para conversar com o dono na Fase 1 (draft)."""

    def __init__(self, executor: StreamingExecutor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def stream_chat(
        self,
        *,
        user_message: str,
        material_summaries: Sequence[str],
        history: Sequence[tuple[str, str]],
        work_context: str,
    ) -> Iterator[str]:
        """Streaming: yields chunks de texto do LLM (resposta surgindo aos poucos).

        O chamador (rota SSE) envia cada chunk como evento SSE e coleta o texto
        completo para persistir e extrair sugestões ao final.
        """
        instruction = _instruction(self._prompt, work_context=work_context)
        content = _content(user_message, material_summaries, history)
        yield from self._executor.stream(instruction, content)


def _instruction(prompt: str, *, work_context: str) -> str:
    """Instrução da persona + work_context (lado do sistema/dono)."""
    parts = [prompt]
    if work_context.strip():
        parts.append(f"Contexto de trabalho do dono: {work_context}")
    return "\n\n".join(parts)


def _content(
    user_message: str,
    material_summaries: Sequence[str],
    history: Sequence[tuple[str, str]],
) -> str:
    """Monta o `untrusted_content`: sumários + histórico + mensagem atual.

    Tudo é não-confiável: os sumários nasceram do arquivo enviado, e o
    histórico contém respostas anteriores do LLM (que leu o material).
    O corte é no INÍCIO do histórico (turnos mais antigos saem primeiro).
    """
    parts: list[str] = []
    if material_summaries:
        parts.append(
            "Materiais do Tema (sumários):\n" + "\n".join(f"- {s}" for s in material_summaries)
        )
    if history:
        history_text = _format_history(history)
        parts.append(history_text)
    parts.append(f"Mensagem do dono: {user_message}")
    return "\n\n".join(parts)


def _format_history(history: Sequence[tuple[str, str]]) -> str:
    """Formata o histórico como turnos, respeitando o teto de caracteres."""
    lines = [f"{role}: {content}" for role, content in history]
    full = "\n".join(lines)
    if len(full) <= _MAX_HISTORY_CHARS:
        return f"Histórico da conversa:\n{full}"
    # Trunca do início (turnos mais antigos saem primeiro).
    truncated = full[-_MAX_HISTORY_CHARS:]
    # Descarta a primeira linha parcial (turno cortado no meio).
    newline = truncated.find("\n")
    if newline != -1:
        truncated = truncated[newline + 1 :]
    _log.info("study.mentor.history_truncated", total=len(full), cap=_MAX_HISTORY_CHARS)
    return f"Histórico da conversa (turnos recentes):\n{truncated}"


def extract_reply(text: str) -> MentorReply:
    """Extrai sugestões das marcações e limpa o texto exibido ao dono."""
    name = _extract_first(_NAME_RE, text)
    focus = _extract_first(_FOCUS_RE, text)
    depth_raw = _extract_first(_DEPTH_RE, text)
    depth = depth_raw if depth_raw and depth_raw in _VALID_DEPTH else None
    clean = _ALL_SUGGESTION_RE.sub("", text).strip()
    return MentorReply(
        text=clean,
        suggested_name=name,
        suggested_focus=focus,
        suggested_depth=depth,
    )


def _extract_first(pattern: re.Pattern[str], text: str) -> str | None:
    """Primeiro match do padrão, sem espaços nas bordas; None se não casa."""
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1).strip()
