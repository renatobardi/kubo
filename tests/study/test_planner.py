"""Persona `planner` (ADR-0043, KUBO-136): capítulos → lições.

Unit puro: o executor é um `_FakeExecutor` (molde de tests/workers/test_distiller.py) —
nenhum teste aqui toca LiteLLM ou rede (CLAUDE.md: "LLMs em testes sempre mockados").

Comportamento fixado: o LLM propõe, o CÓDIGO confere. Uma proposta que não bate com o
sumário do material (seq inexistente, repetido ou fora de ordem) é descartada inteira —
salvar meia proposta daria ao dono um plano que aponta para capítulos que ele não tem.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel, ValidationError
from surrealdb import RecordID

from kubo.errors import ExecutorError, MalformedOutputError, RateLimitExhausted
from kubo.store.study import MaterialChapter
from kubo.study.planner import PlanLesson, Planner, PlanProposal, mechanical_proposal

T = TypeVar("T", bound=BaseModel)

_PROMPT = "agrupe os capítulos em lições"


class _FakeExecutor:
    """Fake de `Executor`: devolve `output` ou levanta `error` na chamada, e registra o
    `untrusted_content` recebido — usado para provar que o sumário chega ao LLM."""

    def __init__(self, output: PlanProposal | None = None, error: Exception | None = None) -> None:
        self._output = output
        self._error = error
        self.received_content: list[str] = []
        self.call_count = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        self.call_count += 1
        self.received_content.append(untrusted_content)
        if self._error is not None:
            raise self._error
        assert self._output is not None, "fake sem output nem erro"
        return cast(T, self._output)


def _chapters(count: int = 4) -> list[MaterialChapter]:
    """Capítulos do material, `seq` 1-based, o primeiro dentro de uma parte."""
    return [
        MaterialChapter(
            id=RecordID("material_chapter", f"c{i}"),
            material=RecordID("material", "m1"),
            seq=i,
            title=f"Capítulo {i}",
            part="Parte I" if i == 1 else None,
            content=f"Conteúdo do capítulo {i}.",
        )
        for i in range(1, count + 1)
    ]


def _proposal(*lessons: tuple[str, list[int]]) -> PlanProposal:
    return PlanProposal(lessons=[PlanLesson(title=t, chapter_seqs=seqs) for t, seqs in lessons])


def test_proposal_rejects_unknown_fields() -> None:
    """`extra="forbid"`: campo inventado pelo LLM não entra no modelo."""
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(
            {"lessons": [{"title": "Aula 1", "chapter_seqs": [1]}], "notes": "oi"}
        )


def test_lesson_requires_at_least_one_chapter() -> None:
    """Lição sem capítulo não é lição — o modelo recusa antes de virar plano."""
    with pytest.raises(ValidationError):
        PlanLesson(title="Aula vazia", chapter_seqs=[])


def test_propose_returns_validated_proposal() -> None:
    """Caminho feliz: a proposta coerente volta como veio, com o sumário no prompt."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2]), ("Prática", [3, 4])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    proposal = planner.propose(_chapters())

    assert proposal is not None
    assert [lesson.title for lesson in proposal.lessons] == ["Fundamentos", "Prática"]
    assert [lesson.chapter_seqs for lesson in proposal.lessons] == [[1, 2], [3, 4]]
    assert "Capítulo 1" in executor.received_content[0]


def test_propose_rejects_chapter_that_does_not_exist() -> None:
    """`seq` fora do material invalida a proposta INTEIRA — nada de plano parcial."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2]), ("Fantasma", [99])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    assert planner.propose(_chapters()) is None


def test_propose_rejects_chapter_repeated_across_lessons() -> None:
    """Capítulo em duas lições é proposta incoerente: o dono estudaria o mesmo duas vezes."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2]), ("Revisão", [2, 3])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    assert planner.propose(_chapters()) is None


def test_propose_rejects_lesson_with_chapters_out_of_order() -> None:
    """Dentro da lição a ordem de leitura tem que ser crescente — o livro tem ordem."""
    executor = _FakeExecutor(output=_proposal(("Bagunça", [3, 1])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    assert planner.propose(_chapters()) is None


@pytest.mark.parametrize(
    "error",
    [
        ExecutorError("provider fora do ar"),
        MalformedOutputError("json inválido"),
        RateLimitExhausted("cota diária esgotada"),
    ],
)
def test_propose_returns_none_when_executor_fails(error: Exception) -> None:
    """Falha de LLM vira None (postura do Finder) — quem chama decide o fallback."""
    planner = Planner(executor=_FakeExecutor(error=error), prompt=_PROMPT)

    assert planner.propose(_chapters()) is None


def test_mechanical_proposal_is_one_lesson_per_chapter_in_order() -> None:
    """Fallback determinístico: 1 capítulo = 1 lição, título e ordem do capítulo."""
    chapters = _chapters(3)

    proposal = mechanical_proposal(chapters)

    assert [lesson.chapter_seqs for lesson in proposal.lessons] == [[1], [2], [3]]
    assert [lesson.title for lesson in proposal.lessons] == [
        "Capítulo 1",
        "Capítulo 2",
        "Capítulo 3",
    ]


def test_mechanical_proposal_respects_chapter_seq_not_list_position() -> None:
    """A ordem vem do `seq`, não da posição na lista devolvida pela store."""
    chapters: Sequence[MaterialChapter] = list(reversed(_chapters(3)))

    proposal = mechanical_proposal(chapters)

    assert [lesson.chapter_seqs for lesson in proposal.lessons] == [[1], [2], [3]]
