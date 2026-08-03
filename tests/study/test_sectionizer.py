"""KUBO-184 — Sectionizer: particiona capítulo em seções tópicas no upload.

Unit puro: o executor é um `_FakeExecutor` (molde de tests/study/test_summarizer.py) —
nenhum teste aqui toca LiteLLM ou rede (CLAUDE.md: "LLMs em testes sempre mockados").

Comportamento fixado: o LLM propõe, o CÓDIGO confere. Seção sem título ou conteúdo
vazio, ou cobertura de tokens < 90% → None (quem chama decide o fallback). O
`anchor_text` é derivado em código dos primeiros ~200 chars do `content`.
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from kubo.errors import ExecutorError
from kubo.study.parsing import ParsedChapter
from kubo.study.sectionizer import (
    SectionItem,
    Sectionizer,
    SectionizerOutput,
    sectionize,
)

_PROMPT = "particione o capítulo em seções"


class _FakeExecutor:
    """Executor de teste: devolve um `SectionizerOutput` pré-definido."""

    def __init__(
        self, output: SectionizerOutput | None = None, error: Exception | None = None
    ) -> None:
        self._output = output
        self._error = error
        self.received_instructions: list[str] = []
        self.received_contents: list[str] = []

    def complete(
        self, instruction: str, untrusted_content: str, response_model: type[BaseModel]
    ) -> BaseModel:
        self.received_instructions.append(instruction)
        self.received_contents.append(untrusted_content)
        if self._error is not None:
            raise self._error
        return cast(BaseModel, self._output)


def _chapter(
    content: str = "Fundamentos. RAG. Tool calling. Orquestração. Guardrails.",
) -> ParsedChapter:
    return ParsedChapter(seq=1, title="Capítulo 1", content=content, part=None)


def _output(*sections: tuple[str, str, str]) -> SectionizerOutput:
    return SectionizerOutput(
        sections=[SectionItem(title=t, content=c, summary=s) for t, c, s in sections]
    )


# --- Schema -----------------------------------------------------------------------------


def test_section_item_rejects_empty_title() -> None:
    """Título vazio é rejeitado pelo schema (min_length=1)."""

    with pytest.raises(ValidationError):
        SectionItem(title="", content="Conteúdo.", summary="Sumário.")


def test_section_item_rejects_empty_content() -> None:
    """Conteúdo vazio é rejeitado pelo schema (min_length=1)."""

    with pytest.raises(ValidationError):
        SectionItem(title="Seção 1", content="", summary="Sumário.")


def test_sectionizer_output_rejects_empty_sections() -> None:
    """Lista de seções vazia é rejeitada (min_length=1) — capítulo tem pelo menos 1."""

    with pytest.raises(ValidationError):
        SectionizerOutput(sections=[])


def test_sectionizer_output_rejects_unknown_fields() -> None:
    """`extra="forbid"`: campo inventado pelo LLM não entra no modelo."""

    with pytest.raises(ValidationError):
        SectionizerOutput.model_validate(
            {"sections": [{"title": "A", "content": "B", "summary": "C"}], "notes": "oi"}
        )


# --- Sectionizer.sectionize (single chapter) --------------------------------------------


def test_sectionize_returns_parts_with_anchor_text_derived() -> None:
    """Sucesso: devolve SectionPart com anchor_text derivado dos primeiros chars do content."""
    content = "Fundamentos. RAG. Tool calling. Orquestração. Guardrails."
    executor = _FakeExecutor(
        output=_output(
            ("Fundamentos", "Fundamentos. RAG.", "Sobre fundamentos."),
            ("Tool calling", "Tool calling. Orquestração. Guardrails.", "Sobre tools."),
        )
    )
    sectionizer = Sectionizer(executor=executor, prompt=_PROMPT)  # type: ignore[arg-type]

    parts = sectionizer.sectionize(_chapter(content))

    assert parts is not None
    assert len(parts) == 2
    assert parts[0].title == "Fundamentos"
    assert parts[0].content == "Fundamentos. RAG."
    assert parts[0].summary == "Sobre fundamentos."
    # anchor_text é derivado dos primeiros chars do content, não pedido ao LLM.
    assert parts[0].anchor_text == "Fundamentos. RAG."
    assert parts[1].anchor_text == "Tool calling. Orquestração. Guardrails."


def test_sectionize_passes_chapter_content_as_untrusted() -> None:
    """O conteúdo do capítulo viaja como untrusted_content (cercado pelo executor)."""
    executor = _FakeExecutor(output=_output(("Seção 1", "Fundamentos. RAG.", "Sumário.")))
    sectionizer = Sectionizer(executor=executor, prompt=_PROMPT)  # type: ignore[arg-type]

    sectionizer.sectionize(_chapter())

    assert _PROMPT in executor.received_instructions[0]
    assert "Capítulo 1" in executor.received_contents[0]
    assert "Fundamentos." in executor.received_contents[0]


def test_sectionize_returns_none_on_executor_failure() -> None:
    """Falha do executor (LLM indisponível) vira None — quem chama decide o fallback."""
    executor = _FakeExecutor(error=ExecutorError("provider down"))
    sectionizer = Sectionizer(executor=executor, prompt=_PROMPT)  # type: ignore[arg-type]

    assert sectionizer.sectionize(_chapter()) is None


def test_sectionize_returns_none_on_validation_error() -> None:
    """JSON inválido do LLM vira ValidationError → None (postura do Summarizer)."""
    error = ValidationError.from_exception_data(
        "SectionizerOutput", [{"type": "missing", "loc": ("sections",), "input": {}}]
    )
    executor = _FakeExecutor(error=error)
    sectionizer = Sectionizer(executor=executor, prompt=_PROMPT)  # type: ignore[arg-type]

    assert sectionizer.sectionize(_chapter()) is None


def test_sectionize_returns_none_when_coverage_below_threshold() -> None:
    """Cobertura < 90% (seções pulam trechos do capítulo) → None."""
    # Capítulo com 10 palavras; seção cobre só 2 → 20% < 90%.
    chapter = _chapter(content="alpha beta gamma delta epsilon zeta eta theta iota kappa")
    executor = _FakeExecutor(output=_output(("Seção 1", "alpha beta", "Só duas palavras.")))
    sectionizer = Sectionizer(executor=executor, prompt=_PROMPT)  # type: ignore[arg-type]

    assert sectionizer.sectionize(chapter) is None


def test_sectionize_accepts_full_coverage() -> None:
    """Cobertura = 100% (seções cobrem o capítulo inteiro) → devolve as partes."""
    content = "Fundamentos. RAG. Tool calling."
    executor = _FakeExecutor(
        output=_output(
            ("Fundamentos", "Fundamentos.", "Sobre fundamentos."),
            ("RAG e Tools", "RAG. Tool calling.", "Sobre RAG e tools."),
        )
    )
    sectionizer = Sectionizer(executor=executor, prompt=_PROMPT)  # type: ignore[arg-type]

    parts = sectionizer.sectionize(_chapter(content))

    assert parts is not None
    assert len(parts) == 2


def test_sectionize_truncates_long_chapter_content() -> None:
    """Conteúdo de capítulo muito longo é truncado antes de ir ao prompt (controle de custo)."""
    long_content = "x" * 200_000  # 200KB — bem acima do teto
    executor = _FakeExecutor(output=_output(("Seção 1", "x" * 100, "Sumário.")))
    sectionizer = Sectionizer(executor=executor, prompt=_PROMPT)  # type: ignore[arg-type]

    sectionizer.sectionize(_chapter(long_content))

    # O conteúdo enviado ao executor é truncado, não vai o texto inteiro.
    assert len(executor.received_contents[0]) < 200_000


# --- sectionize (module-level, all chapters + fallback) ---------------------------------


def test_sectionize_returns_dict_with_all_chapters() -> None:
    """sectionize processa todos os capítulos e devolve dict por chapter.seq."""
    executor = _FakeExecutor(output=_output(("Seção A", "Fundamentos. RAG.", "Sumário A.")))
    chapters = [
        ParsedChapter(seq=1, title="Cap 1", content="Fundamentos. RAG.", part=None),
        ParsedChapter(seq=2, title="Cap 2", content="Tool calling. Guardrails.", part=None),
    ]

    result = sectionize(executor=executor, prompt=_PROMPT, chapters=chapters)  # type: ignore[arg-type]

    assert set(result.keys()) == {1, 2}
    assert all(len(parts) >= 1 for parts in result.values())


def test_sectionize_falls_back_to_one_section_on_failure() -> None:
    """Sectionizer falha para um capítulo → fallback: 1 seção = capítulo inteiro."""
    executor = _FakeExecutor(error=ExecutorError("provider down"))
    chapter = ParsedChapter(seq=1, title="Cap 1", content="Conteúdo do capítulo.", part=None)

    result = sectionize(executor=executor, prompt=_PROMPT, chapters=[chapter])  # type: ignore[arg-type]

    assert 1 in result
    assert len(result[1]) == 1
    part = result[1][0]
    assert part.title == "Cap 1"
    assert part.content == "Conteúdo do capítulo."
    assert part.anchor_text == ""
    assert part.summary == "Cap 1"


def test_sectionize_mixed_success_and_fallback() -> None:
    """Um capítulo sectioniza, outro falha → dict tem ambos com fallback no que falhou."""
    call_count = 0

    class _MixedExecutor:
        def __init__(self) -> None:
            self.received: list[str] = []

        def complete(
            self, instruction: str, untrusted_content: str, response_model: type[BaseModel]
        ) -> BaseModel:
            nonlocal call_count
            self.received.append(untrusted_content)
            call_count += 1
            if call_count == 1:
                return _output(("Seção A", "Fundamentos. RAG.", "Sumário."))
            raise ExecutorError("provider down on 2nd call")

    chapters = [
        ParsedChapter(seq=1, title="Cap 1", content="Fundamentos. RAG.", part=None),
        ParsedChapter(seq=2, title="Cap 2", content="Tool calling.", part=None),
    ]

    result = sectionize(executor=_MixedExecutor(), prompt=_PROMPT, chapters=chapters)  # type: ignore[arg-type]

    assert len(result[1]) == 1  # sectionized successfully
    assert result[1][0].title == "Seção A"
    assert len(result[2]) == 1  # fallback
    assert result[2][0].title == "Cap 2"
    assert result[2][0].content == "Tool calling."
