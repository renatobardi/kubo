"""Persona `sectionizer` (KUBO-184, ADR-0048): capítulo → seções tópicas.

Particiona o conteúdo de um capítulo em seções tópicas reais — as divisões
naturais do texto (ex.: 'Fundamentos', 'RAG e tool calling', 'Orquestração'),
não fatiamento arbitrário por tamanho. Molde do `Summarizer`: classe fina sobre
um `Executor`, sem flow e sem banco.

De que lado cada dado viaja (mesma pilha do Summarizer/Tutor):
- `untrusted_content` leva o texto do capítulo (nasceu do arquivo enviado);
- a instrução leva só o prompt da persona (do sistema).

Validação de cobertura em código: a concatenação das seções deve cobrir ≥ 90%
dos tokens do capítulo (sobre o texto efetivamente enviado ao LLM). Falha de
LLM ou cobertura insuficiente vira None — quem chama decide o fallback
(1 seção = capítulo inteiro).
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kubo.errors import ExecutorError
from kubo.executors.base import Executor
from kubo.study.parsing import ParsedChapter, SectionPart

_log = structlog.get_logger(__name__)

# Teto de caracteres do capítulo que vai ao prompt. Controle de custo e
# compatibilidade com max_tokens de output: o prompt pede o conteúdo ecoado
# no JSON, então a saída é ≥ entrada. 8192 tokens ≈ 32k chars de output →
# limitamos a entrada a 20k para deixar margem de JSON + título.
_MAX_PROMPT_TEXT = 20_000

# Fração mínima de tokens do capítulo que a concatenação das seções deve cobrir.
# Abaixo disso, o sectionizer devolve None (fallback gracioso).
_COVERAGE_THRESHOLD = 0.90

# Teto do anchor_text derivado em código dos primeiros chars do content.
_ANCHOR_MAX_CHARS = 200

# Teto de capítulos sectionizados por material (KUBO-184): sem isso, um epub
# com centenas de capítulos faz N chamadas LLM sequenciais no request de upload
# e estoura o timeout do proxy. Capítulos além do limite recebem fallback.
_MAX_CHAPTERS_TO_SECTIONIZE = 20


class SectionItem(BaseModel):
    """Uma seção tópica produzida pelo LLM: título, conteúdo e sumário curto."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=500_000)
    summary: str = Field(min_length=1, max_length=200)


class SectionizerOutput(BaseModel):
    """Saída estruturada do sectionizer: lista de seções (pelo menos 1)."""

    model_config = ConfigDict(extra="forbid")

    sections: list[SectionItem] = Field(min_length=1, max_length=100)


class Sectionizer:
    """Envolve um `Executor` para particionar um capítulo em seções tópicas."""

    def __init__(self, executor: Executor, prompt: str) -> None:
        self._executor = executor
        self._prompt = prompt

    def sectionize(self, chapter: ParsedChapter) -> list[SectionPart] | None:
        """Seções validadas com `anchor_text` derivado, ou None se falhar.

        None = LLM indisponível, JSON inválido, ValidationError, ou cobertura
        de tokens < 90%. Quem chama decide o fallback (1 seção = capítulo).
        """
        truncated, content = _content(chapter)
        try:
            output = self._executor.complete(self._prompt, content, SectionizerOutput)
        except (ExecutorError, ValidationError):
            _log.info("study.sectionizer.failed", chapter=chapter.seq)
            return None
        if not _is_coherent(truncated, output.sections):
            _log.info("study.sectionizer.incoherent", chapter=chapter.seq)
            return None
        return [
            SectionPart(
                title=s.title,
                anchor_text=_anchor_text(s.content),
                content=s.content,
                summary=s.summary,
            )
            for s in output.sections
        ]


def sectionize(
    *, executor: Executor, prompt: str, chapters: list[ParsedChapter]
) -> dict[int, list[SectionPart]]:
    """Particiona todos os capítulos; fallback gracioso em falha individual.

    Devolve `dict[chapter.seq, list[SectionPart]]` — todo capítulo tem entrada.
    Capítulo onde o sectionizer falhou (ou além do cap de capítulos sectionizados)
    recebe 1 seção fallback (content = capítulo inteiro).
    """
    sectionizer = Sectionizer(executor=executor, prompt=prompt)
    result: dict[int, list[SectionPart]] = {}
    for i, chapter in enumerate(chapters):
        if i >= _MAX_CHAPTERS_TO_SECTIONIZE:
            result[chapter.seq] = [fallback_part(chapter)]
            continue
        parts = sectionizer.sectionize(chapter)
        if parts is None:
            parts = [fallback_part(chapter)]
        result[chapter.seq] = parts
    return result


def fallback_part(chapter: ParsedChapter) -> SectionPart:
    """1 seção = capítulo inteiro (mesmo fallback do ADR-0048 §6).

    Ponto único de verdade — a store reusa este helper em vez de duplicar.
    """
    return SectionPart(
        title=chapter.title,
        anchor_text="",
        content=chapter.content,
        summary=chapter.title,
    )


def _content(chapter: ParsedChapter) -> tuple[str, str]:
    """Monta o `untrusted_content`: título truncado + conteúdo truncado ao teto.

    Devolve (texto_truncado, prompt_content) — o texto truncado é o que
    efetivamente foi enviado ao LLM, usado para validar cobertura.
    """
    title = chapter.title[:300]
    text = chapter.content[:_MAX_PROMPT_TEXT]
    truncated = text
    return truncated, f"[{chapter.seq}] {title}\n{text}"


def _anchor_text(content: str) -> str:
    """Deriva `anchor_text` dos primeiros chars do content (não pedido ao LLM)."""
    return content[:_ANCHOR_MAX_CHARS]


def _coverage(chapter_content: str, sections_content: str) -> float:
    """Fração de tokens (palavras) do capítulo presentes na concatenação das seções."""
    ch_tokens = set(chapter_content.split())
    if not ch_tokens:
        return 1.0
    sec_tokens = set(sections_content.split())
    return len(ch_tokens & sec_tokens) / len(ch_tokens)


def _is_coherent(chapter_content: str, sections: list[SectionItem]) -> bool:
    """Validação em código: títulos/conteúdos não-vazios e cobertura ≥ 90%."""
    if not sections:
        return False
    for s in sections:
        if not s.title.strip() or not s.content.strip():
            return False
    concatenated = " ".join(s.content for s in sections)
    return _coverage(chapter_content, concatenated) >= _COVERAGE_THRESHOLD
