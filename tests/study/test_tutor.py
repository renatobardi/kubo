"""Persona `tutor` (ADR-0043, KUBO-137): capítulos → a lição do dia.

Unit puro: o executor é um `_FakeExecutor` (molde de tests/study/test_planner.py) —
nenhum teste aqui toca LiteLLM ou rede (CLAUDE.md: "LLMs em testes sempre mockados").

Dois comportamentos são de segurança, não de conveniência:
1. o texto dos capítulos (arquivo que o dono enviou, entrada hostil) viaja como
   `untrusted_content`, e o contexto de trabalho / os erros recentes — dado do DONO —
   viajam na instrução. Trocar os lados faria o conteúdo coletado ditar a instrução;
2. o volume enviado ao provedor tem teto: um "capítulo" com o livro inteiro dentro não
   pode transformar o custo da lição em refém do arquivo.
"""

from __future__ import annotations

from typing import TypeVar, cast

import pytest
from pydantic import BaseModel, ValidationError
from surrealdb import RecordID

from kubo.errors import ExecutorError, MalformedOutputError, RateLimitExhausted
from kubo.store.study import MaterialChapter
from kubo.study.tutor import LessonOutput, QuizItem, Tutor

T = TypeVar("T", bound=BaseModel)

_PROMPT = "ensine a lição do dia"
_WORK_CONTEXT = "Arquiteto de plataforma; times pequenos e dados em escala."
# Marcador no FIM do último capítulo: se ele chega ao prompt, nada foi truncado.
_TAIL = "MARCADOR-DE-CAUDA"


class _FakeExecutor:
    """Fake de `Executor`: devolve `output` ou levanta `error`, e registra a instrução e
    o `untrusted_content` recebidos — é o que prova de que lado cada dado viaja."""

    def __init__(self, output: LessonOutput | None = None, error: Exception | None = None) -> None:
        self._output = output
        self._error = error
        self.received_instructions: list[str] = []
        self.received_content: list[str] = []

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        self.received_instructions.append(instruction)
        self.received_content.append(untrusted_content)
        if self._error is not None:
            raise self._error
        assert self._output is not None, "fake sem output nem erro"
        return cast(T, self._output)


def _chapters(count: int = 2, *, body: str = "Conteúdo do capítulo") -> list[MaterialChapter]:
    """Capítulos da lição, `seq` 1-based; o último termina no marcador de cauda."""
    return [
        MaterialChapter(
            id=RecordID("material_chapter", f"c{i}"),
            material=RecordID("material", "m1"),
            seq=i,
            title=f"Capítulo {i}",
            part=None,
            content=f"{body} {i}." + (_TAIL if i == count else ""),
        )
        for i in range(1, count + 1)
    ]


def _quiz(*, answer_index: int = 0, options: int = 3) -> list[QuizItem]:
    return [
        QuizItem(
            question=f"Pergunta {i}?",
            options=[f"Opção {j}" for j in range(options)],
            answer_index=answer_index,
            explanation=f"Porque sim {i}.",
        )
        for i in (1, 2)
    ]


def _lesson(*, recap: str | None = None, quiz: list[QuizItem] | None = None) -> LessonOutput:
    return LessonOutput(
        concept="O conceito destilado.",
        scenario="Um cenário concreto.",
        application="Como aplicar amanhã.",
        recap=recap,
        quiz=_quiz() if quiz is None else quiz,
    )


def _tutor(executor: _FakeExecutor) -> Tutor:
    return Tutor(executor=executor, prompt=_PROMPT)


def _generate(tutor: Tutor, *, misses: list[str] | None = None) -> LessonOutput | None:
    return tutor.generate(
        entry_title="Aula 3 — Filas",
        chapters=_chapters(),
        work_context=_WORK_CONTEXT,
        misses=misses or [],
    )


# --- Contrato dos modelos --------------------------------------------------------------


def test_lesson_output_rejects_unknown_fields() -> None:
    """`extra="forbid"`: campo inventado pelo LLM não entra na lição."""
    with pytest.raises(ValidationError):
        LessonOutput.model_validate(
            {
                "concept": "c",
                "scenario": "s",
                "application": "a",
                "quiz": [q.model_dump() for q in _quiz()],
                "notes": "oi",
            }
        )


def test_lesson_output_requires_at_least_two_questions() -> None:
    """Quiz de uma questão só não é quiz — o modelo recusa antes de virar lição."""
    with pytest.raises(ValidationError):
        LessonOutput.model_validate(
            {
                "concept": "c",
                "scenario": "s",
                "application": "a",
                "quiz": [_quiz()[0].model_dump()],
            }
        )


def test_quiz_item_requires_at_least_two_options() -> None:
    """Questão com uma alternativa só não dá escolha ao dono."""
    with pytest.raises(ValidationError):
        QuizItem(question="Só uma?", options=["Sim"], answer_index=0, explanation="Porque sim.")


def test_recap_is_optional() -> None:
    """Lição sem erro recente nasce sem recapitulação — `recap` ausente é válido."""
    assert _lesson().recap is None


# --- Geração ---------------------------------------------------------------------------


def test_generate_returns_the_validated_lesson() -> None:
    """Caminho feliz: a lição coerente volta como veio."""
    executor = _FakeExecutor(output=_lesson())

    lesson = _generate(_tutor(executor))

    assert lesson is not None
    assert lesson.concept == "O conceito destilado."
    assert len(lesson.quiz) == 2


def test_chapter_text_travels_as_untrusted_content() -> None:
    """O texto do material vai no `untrusted_content` — nunca na instrução."""
    executor = _FakeExecutor(output=_lesson())

    _generate(_tutor(executor))

    assert "Conteúdo do capítulo 1." in executor.received_content[0]
    assert "Conteúdo do capítulo 1." not in executor.received_instructions[0]


def test_owner_context_and_misses_travel_in_the_instruction() -> None:
    """Contexto de trabalho, erros recentes e título da lição são dado do DONO: instrução."""
    executor = _FakeExecutor(output=_lesson(recap="Revisando o que você errou."))

    _generate(_tutor(executor), misses=["O que é backpressure?"])

    instruction = executor.received_instructions[0]
    assert _WORK_CONTEXT in instruction
    assert "O que é backpressure?" in instruction
    assert "Aula 3 — Filas" in instruction
    assert "O que é backpressure?" not in executor.received_content[0]


def test_generate_without_misses_still_produces_a_lesson_without_recap() -> None:
    """Sem erro recente não há recapitulação — e a ausência dela NÃO invalida a lição."""
    executor = _FakeExecutor(output=_lesson(recap=None))

    lesson = _generate(_tutor(executor), misses=[])

    assert lesson is not None
    assert lesson.recap is None


def test_generate_rejects_an_answer_index_outside_the_options() -> None:
    """`answer_index` fora da faixa vira None: um quiz assim nunca poderia ser acertado.

    Só o modelo não pega — `ge=0` não sabe quantas alternativas existem. A conferência
    é em código, como o `_is_coherent` do planner."""
    executor = _FakeExecutor(output=_lesson(quiz=_quiz(answer_index=3, options=3)))

    assert _generate(_tutor(executor)) is None


@pytest.mark.parametrize(
    "error",
    [
        ExecutorError("provider fora do ar"),
        MalformedOutputError("json inválido"),
        RateLimitExhausted("cota diária esgotada"),
        # O executor valida a saída contra `LessonOutput` e repassa a ValidationError
        # (ex.: quiz de uma questão só, fixado em `test_lesson_output_requires_*`).
        ValidationError.from_exception_data("LessonOutput", []),
    ],
)
def test_generate_returns_none_when_executor_fails(error: Exception) -> None:
    """Falha de LLM vira None — o job da véspera loga e tenta de novo na próxima janela."""
    assert _generate(_tutor(_FakeExecutor(error=error))) is None


def test_chapter_text_is_capped_before_reaching_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O conteúdo enviado respeita `_MAX_PROMPT_TEXT` e o corte é NO FIM.

    O teto é lido do módulo a cada chamada (constante monkeypatchada aqui): um material
    mal parseado não pode empurrar o livro inteiro para dentro do prompt.
    """
    cap = 120
    monkeypatch.setattr("kubo.study.tutor._MAX_PROMPT_TEXT", cap)
    executor = _FakeExecutor(output=_lesson())
    tutor = _tutor(executor)

    tutor.generate(
        entry_title="Aula 3",
        chapters=_chapters(4, body="x" * 200),
        work_context=_WORK_CONTEXT,
        misses=[],
    )

    content = executor.received_content[0]
    assert len(content) <= cap
    assert _TAIL not in content


def test_short_content_is_not_truncated() -> None:
    """Controle do teste acima: abaixo do teto, o material chega inteiro (cauda incluída)."""
    executor = _FakeExecutor(output=_lesson())

    _generate(_tutor(executor))

    assert _TAIL in executor.received_content[0]
