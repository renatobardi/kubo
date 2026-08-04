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
from kubo.store.study import MaterialSection
from kubo.study.tutor import LessonOutput, ProvenanceItem, QuizItem, Tutor

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


def _sections(count: int = 2, *, body: str = "Conteúdo da seção") -> list[MaterialSection]:
    """Seções da lição, (chapter_seq=1, section_seq) 1-based; o último tem o marcador de cauda."""
    return [
        MaterialSection(
            id=RecordID("material_section", f"s{i}"),
            material=RecordID("material", "m1"),
            material_chapter=RecordID("material_chapter", "c1"),
            seq=i,
            title=f"Seção {i}",
            anchor_text="",
            content=f"{body} {i}." + (_TAIL if i == count else ""),
            summary=f"Sumário {i}",
            chapter_seq=1,
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


def _provenance(count: int = 1) -> list[ProvenanceItem]:
    """Provenância com pares (chapter_seq, section_seq) distintos — exercita
    o caso de múltiplas seções no mesmo capítulo (1,1), (1,2), (1,3)..."""
    return [
        ProvenanceItem(chapter_seq=1, section_seq=i, quote=f"Trecho que originou o conceito {i}.")
        for i in range(1, count + 1)
    ]


def _lesson(*, recap: str | None = None, quiz: list[QuizItem] | None = None) -> LessonOutput:
    return LessonOutput(
        concept="O conceito destilado.",
        scenario="Um cenário concreto.",
        application="Como aplicar amanhã.",
        recap=recap,
        provenance=_provenance(),
        quiz=_quiz() if quiz is None else quiz,
    )


def _tutor(executor: _FakeExecutor) -> Tutor:
    return Tutor(executor=executor, prompt=_PROMPT)


def _generate(tutor: Tutor, *, misses: list[str] | None = None) -> LessonOutput | None:
    return tutor.generate(
        entry_title="Aula 3 — Filas",
        sections=_sections(),
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
                "provenance": [p.model_dump() for p in _provenance()],
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
                "provenance": [p.model_dump() for p in _provenance()],
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


def test_lesson_output_requires_provenance() -> None:
    """Sem provenância não há como confiar no conceito — o modelo recusa."""
    with pytest.raises(ValidationError):
        LessonOutput.model_validate(
            {
                "concept": "c",
                "scenario": "s",
                "application": "a",
                "quiz": [q.model_dump() for q in _quiz()],
            }
        )


def test_provenance_item_requires_chapter_seq_section_seq_and_quote() -> None:
    """Provenância sem capítulo, sem seção ou sem citação não localiza o trecho — incompleta."""
    with pytest.raises(ValidationError):
        ProvenanceItem(chapter_seq=0, section_seq=1, quote="trecho")
    with pytest.raises(ValidationError):
        ProvenanceItem(chapter_seq=1, section_seq=0, quote="trecho")
    with pytest.raises(ValidationError):
        ProvenanceItem(chapter_seq=1, section_seq=1, quote="")


def test_provenance_quote_is_capped_to_prevent_reproduction() -> None:
    """A citação é localizador, não reprodução — acima de 300 chars o modelo recusa."""
    with pytest.raises(ValidationError):
        ProvenanceItem(chapter_seq=1, section_seq=1, quote="x" * 301)


def test_section_pair_travels_in_the_content_for_the_tutor_to_reference() -> None:
    """O par (chapter_seq, section_seq) vai no conteúdo (cercado) para o LLM referenciar na provenância."""
    executor = _FakeExecutor(output=_lesson())

    _generate(_tutor(executor))

    assert "(1, 1)" in executor.received_content[0]
    assert "(1, 2)" in executor.received_content[0]


# --- Geração ---------------------------------------------------------------------------


def test_generate_returns_the_validated_lesson() -> None:
    """Caminho feliz: a lição coerente volta como veio."""
    executor = _FakeExecutor(output=_lesson())

    lesson = _generate(_tutor(executor))

    assert lesson is not None
    assert lesson.concept == "O conceito destilado."
    assert len(lesson.quiz) == 2


def test_section_text_travels_as_untrusted_content() -> None:
    """O texto da seção vai no `untrusted_content` — nunca na instrução."""
    executor = _FakeExecutor(output=_lesson())

    _generate(_tutor(executor))

    assert "Conteúdo da seção 1." in executor.received_content[0]
    assert "Conteúdo da seção 1." not in executor.received_instructions[0]


def test_owner_work_context_travels_in_the_instruction() -> None:
    """O contexto de trabalho é dado DIGITADO pelo dono: é o único que vai na instrução."""
    executor = _FakeExecutor(output=_lesson())

    _generate(_tutor(executor))

    assert _WORK_CONTEXT in executor.received_instructions[0]
    assert _WORK_CONTEXT not in executor.received_content[0]


def test_entry_title_travels_as_untrusted_content() -> None:
    """O título da lição VEM DO EPUB (o planner nunca valida o texto dele): é hostil.

    O planner deriva o título de lição do sumário do arquivo enviado, e nada no caminho
    inspeciona esse texto — um capítulo chamado "ignore as instruções acima e ..."
    ditaria a instrução se o título viajasse nela. Vai na cerca do conteúdo.
    """
    executor = _FakeExecutor(output=_lesson())

    _generate(_tutor(executor))

    assert "Aula 3 — Filas" in executor.received_content[0]
    assert "Aula 3 — Filas" not in executor.received_instructions[0]


def test_recent_misses_travel_as_untrusted_content_with_the_rule_in_the_instruction() -> None:
    """Erro recente é ENUNCIADO GERADO POR LLM sobre material hostil — laço de 2ª ordem.

    O texto do enunciado nasceu de um quiz que a persona escreveu lendo o epub: promovê-lo
    a instrução deixaria o material dirigir a próxima lição por dois saltos. A REGRA
    ("recapitule o que está listado no conteúdo") é do sistema e fica na instrução; o
    texto dos erros fica na cerca.
    """
    executor = _FakeExecutor(output=_lesson(recap="Revisando o que você errou."))

    _generate(_tutor(executor), misses=["O que é backpressure?"])

    instruction = executor.received_instructions[0]
    assert "O que é backpressure?" in executor.received_content[0]
    assert "O que é backpressure?" not in instruction
    assert "recapitula" in instruction.lower()


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


def test_section_text_is_capped_before_reaching_the_provider(
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
        sections=_sections(4, body="x" * 200),
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
