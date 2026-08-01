"""KUBO-162 — Sumarizador de Material: gera sumário síncrono no upload.

Unit com executor mockado. O sumário é consumido por `mentor` (Fase 1) e
`planner` (Fase 2) — não é o conteúdo completo, é uma destilação.
"""

from __future__ import annotations

from pydantic import BaseModel

from kubo.errors import ExecutorError
from kubo.study.parsing import ParsedChapter, ParsedMaterial
from kubo.study.summarizer import Summarizer, SummaryOutput


class _FakeExecutor:
    """Executor de teste: devolve um `SummaryOutput` pré-definido."""

    def __init__(self, output: SummaryOutput) -> None:
        self._output = output
        self.last_instruction: str | None = None
        self.last_content: str | None = None

    def complete(
        self, instruction: str, untrusted_content: str, response_model: type[BaseModel]
    ) -> SummaryOutput:
        self.last_instruction = instruction
        self.last_content = untrusted_content
        return self._output


def _parsed(title: str = "Manual de Kubo", n_chapters: int = 3) -> ParsedMaterial:
    return ParsedMaterial(
        title=title,
        chapters=[
            ParsedChapter(
                seq=i, title=f"Capítulo {i}", content=f"Conteúdo do capítulo {i}.", part=None
            )
            for i in range(n_chapters)
        ],
    )


def test_summarizer_generates_summary() -> None:
    """Sumarizador devolve o sumário gerado pelo executor."""
    expected = SummaryOutput(summary="Um guia sobre agentes e ateliê pessoal.")
    executor = _FakeExecutor(expected)
    summarizer = Summarizer(executor=executor, prompt="Resuma o material.")  # type: ignore[arg-type]

    result = summarizer.generate(_parsed())

    assert result == "Um guia sobre agentes e ateliê pessoal."


def test_summarizer_passes_chapter_content_as_untrusted() -> None:
    """O conteúdo dos capítulos viaja como untrusted_content (cercado pelo executor)."""
    executor = _FakeExecutor(SummaryOutput(summary="Resumo."))
    summarizer = Summarizer(executor=executor, prompt="Resuma.")  # type: ignore[arg-type]

    summarizer.generate(_parsed(n_chapters=2))

    assert executor.last_content is not None
    assert "Capítulo 0" in executor.last_content
    assert "Capítulo 1" in executor.last_content


def test_summarizer_returns_none_on_executor_failure() -> None:
    """Falha do executor vira None (postura do Tutor/Planner): quem chama decide."""

    class _FailingExecutor:
        def complete(
            self, instruction: str, untrusted_content: str, response_model: type[BaseModel]
        ) -> BaseModel:
            raise ExecutorError("provider down")

    summarizer = Summarizer(executor=_FailingExecutor(), prompt="Resuma.")  # type: ignore[arg-type]

    assert summarizer.generate(_parsed()) is None


def test_summary_output_schema_rejects_empty_summary() -> None:
    """Sumário vazio é rejeitado pelo schema (min_length=1)."""
    import pytest

    with pytest.raises(ValueError):
        SummaryOutput(summary="")


def test_summarizer_truncates_long_chapter_content() -> None:
    """Conteúdo de capítulos muito longos é truncado antes de ir ao prompt (controle de custo)."""
    long_content = "x" * 200_000  # 200KB — bem acima do teto
    parsed = ParsedMaterial(
        title="Grande",
        chapters=[ParsedChapter(seq=0, title="Grande", content=long_content, part=None)],
    )
    executor = _FakeExecutor(SummaryOutput(summary="Resumo."))
    summarizer = Summarizer(executor=executor, prompt="Resuma.")  # type: ignore[arg-type]

    summarizer.generate(parsed)

    # O conteúdo enviado ao executor é truncado, não vai o texto inteiro.
    assert executor.last_content is not None
    assert len(executor.last_content) < 200_000
