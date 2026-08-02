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
from kubo.study.planner import (
    PlanLesson,
    Planner,
    PlannerChatReply,
    PlanProposal,
    mechanical_proposal,
)

T = TypeVar("T", bound=BaseModel)

_PROMPT = "agrupe os capítulos em lições"


class _FakeExecutor:
    """Fake de `Executor`: devolve `output` ou levanta `error` na chamada, e registra o
    `untrusted_content` recebido — usado para provar que o sumário chega ao LLM."""

    def __init__(
        self,
        output: PlanProposal | PlannerChatReply | None = None,
        error: Exception | None = None,
    ) -> None:
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


def test_summary_is_capped_before_reaching_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """O prefixo (focus/depth/transcript) é truncado, mas o bloco de capítulos NUNCA.

    Os títulos vêm do arquivo que o dono enviou e nada os limita antes daqui: sem o teto,
    um epub com milhares de "capítulos" faria o custo da proposta refém do upload. O teto
    truncava o prompt inteiro; KUBO-165 mudou para truncar só o prefixo, preservando o
    bloco de capítulos (AC8 — sem ele o planner não tem o que agrupar).
    """
    cap = 100
    monkeypatch.setattr("kubo.study.planner._MAX_SUMMARY_TEXT", cap)
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2])))

    Planner(executor=executor, prompt=_PROMPT).propose(_chapters(80))

    content = executor.received_content[0]
    # O bloco de capítulos é preservado integralmente (pode exceder o cap).
    assert "80. Capítulo 80" in content
    # Mas o prefixo (focus/depth/transcript) foi truncado — só há capítulos.
    assert "Foco" not in content


def test_a_short_summary_is_not_truncated() -> None:
    """Controle do teste acima: abaixo do teto, o sumário chega inteiro."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2])))

    Planner(executor=executor, prompt=_PROMPT).propose(_chapters(4))

    assert "4. Capítulo 4" in executor.received_content[0]


# --- KUBO-164: novo input (campos + resumo do mentor + sumários) -------------------------


def test_propose_includes_focus_and_depth_in_prompt() -> None:
    """Focus e depth do Tema chegam ao prompt do planner (ADR-0047 §2)."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _chapters(),
        focus="sistemas agênticos",
        depth="aprofundado",
        mentor_transcript="",
        material_summaries=[],
    )

    content = executor.received_content[0]
    assert "sistemas agênticos" in content
    assert "aprofundado" in content


def test_propose_includes_mentor_transcript_in_prompt() -> None:
    """O transcript da conversa com mentor chega ao prompt do planner."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _chapters(),
        focus=None,
        depth=None,
        mentor_transcript="Dono: Quero focar em agentes. Mentor: Vou sugerir um plano prático.",
        material_summaries=[],
    )

    content = executor.received_content[0]
    assert "Quero focar em agentes" in content


def test_propose_includes_material_summaries_in_prompt() -> None:
    """Os sumários dos materiais chegam ao prompt do planner."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _chapters(),
        focus=None,
        depth=None,
        mentor_transcript="",
        material_summaries=["Um guia sobre agentes.", "Livro avançado de LLMs."],
    )

    content = executor.received_content[0]
    assert "Um guia sobre agentes." in content
    assert "Livro avançado de LLMs." in content


def test_propose_includes_planning_history_in_prompt() -> None:
    """KUBO-165: a conversa da Fase 2 chega ao prompt do planner no repropose."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _chapters(),
        focus=None,
        depth=None,
        mentor_transcript="",
        material_summaries=[],
        planning_history=[("user", "junta tudo numa lição"), ("assistant", "ok, juntei")],
    )

    content = executor.received_content[0]
    assert "junta tudo numa lição" in content
    assert "Conversa da Fase 2" in content


def test_propose_without_optional_fields_still_works() -> None:
    """Propose sem os novos campos opcionais continua funcionando (compatibilidade)."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [1, 2])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    proposal = planner.propose(_chapters())

    assert proposal is not None
    assert "Capítulo 1" in executor.received_content[0]


# --- KUBO-165: chat com planner (Fase 2 — ajuste incremental) ---------------------------


def _chat_reply(text: str, *lessons: tuple[str, list[int]]) -> PlannerChatReply:
    return PlannerChatReply(
        text=text,
        lessons=[PlanLesson(title=t, chapter_seqs=s) for t, s in lessons] or None,
    )


def test_chat_returns_text_and_updated_plan() -> None:
    """Chat com planner devolve texto + plano atualizado coerente."""
    executor = _FakeExecutor(
        output=_chat_reply("Juntei as lições 1 e 2.", ("Fundamentos", [1, 2, 3, 4]))
    )
    planner = Planner(executor=executor, prompt=_PROMPT)

    reply = planner.chat(
        user_message="junta lição 1 e 2",
        chapters=_chapters(),
        current_plan=[("Fundamentos", [1, 2]), ("Prática", [3, 4])],
    )

    assert reply is not None
    assert reply.text == "Juntei as lições 1 e 2."
    assert reply.lessons is not None
    assert len(reply.lessons) == 1
    assert reply.lessons[0].chapter_seqs == [1, 2, 3, 4]


def test_chat_without_plan_update_returns_text_only() -> None:
    """Mensagem que não toca o plano devolve texto sem lessons."""
    executor = _FakeExecutor(output=_chat_reply("Entendi, vou manter o plano."))
    planner = Planner(executor=executor, prompt=_PROMPT)

    reply = planner.chat(
        user_message="o que acha do plano?",
        chapters=_chapters(),
        current_plan=[("Fundamentos", [1, 2])],
    )

    assert reply is not None
    assert reply.text == "Entendi, vou manter o plano."
    assert reply.lessons is None


def test_chat_includes_current_plan_in_prompt() -> None:
    """O plano atual chega ao prompt para ajuste incremental."""
    executor = _FakeExecutor(output=_chat_reply("Ok."))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.chat(
        user_message="ajusta",
        chapters=_chapters(),
        current_plan=[("Fundamentos", [1, 2]), ("Prática", [3, 4])],
    )

    content = executor.received_content[0]
    assert "Fundamentos" in content
    assert "Prática" in content


def test_chat_includes_planning_history_in_prompt() -> None:
    """O histórico da conversa com planner chega ao prompt."""
    executor = _FakeExecutor(output=_chat_reply("Ok."))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.chat(
        user_message="muda de novo",
        chapters=_chapters(),
        current_plan=[("L1", [1])],
        planning_history=[("user", "junta tudo"), ("assistant", "ok, juntei")],
    )

    content = executor.received_content[0]
    assert "junta tudo" in content


def test_chat_rejects_incoherent_plan_update() -> None:
    """Plano atualizado com seq inexistente é descartado — lessons vira None."""
    executor = _FakeExecutor(output=_chat_reply("Aqui está.", ("Fantasma", [99])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    reply = planner.chat(
        user_message="adiciona capítulo 99",
        chapters=_chapters(),
        current_plan=[("L1", [1])],
    )

    assert reply is not None
    assert reply.text == "Aqui está."
    assert reply.lessons is None  # plano incoerente descartado, texto preservado


def test_chat_returns_none_on_executor_failure() -> None:
    """Falha de LLM vira None — quem chama decide o que fazer."""
    planner = Planner(
        executor=_FakeExecutor(error=ExecutorError("provider fora")),
        prompt=_PROMPT,
    )

    assert (
        planner.chat(
            user_message="ajusta",
            chapters=_chapters(),
            current_plan=[("L1", [1])],
        )
        is None
    )


# --- KUBO-168: streaming do planner ------------------------------------------------------


class _FakeStreamingExecutor:
    """Fake de `StreamingExecutor`: devolve `chunks` ou levanta `error`."""

    def __init__(self, chunks: list[str], error: Exception | None = None) -> None:
        self._chunks = chunks
        self._error = error
        self.received_instructions: list[str] = []
        self.received_content: list[str] = []

    def stream(self, instruction: str, untrusted_content: str) -> list[str]:
        self.received_instructions.append(instruction)
        self.received_content.append(untrusted_content)
        if self._error is not None:
            raise self._error
        return self._chunks


def test_stream_chat_yields_text_chunks() -> None:
    """stream_chat devolve chunks de texto do LLM (streaming)."""
    from kubo.study.planner import Planner

    stream_executor = _FakeStreamingExecutor(chunks=["Juntei ", "as lições."])
    planner = Planner(executor=_FakeExecutor(), prompt=_PROMPT)

    chunks = list(
        planner.stream_chat(
            stream_executor,
            user_message="junta lição 1 e 2",
            chapters=_chapters(),
            current_plan=[("L1", [1, 2])],
        )
    )
    assert chunks == ["Juntei ", "as lições."]


def test_stream_chat_with_plan_block_extracts_reply() -> None:
    """stream_chat com bloco JSON no final → extract_planner_reply devolve texto + plano."""
    from kubo.study.planner import extract_planner_reply

    full_text = (
        "Juntei as lições 1 e 2 numa só.\n\n"
        '```json\n{"lessons": [{"title": "Fundamentos", "chapter_seqs": [1, 2, 3, 4]}]}\n```'
    )
    reply = extract_planner_reply(full_text, _chapters())
    assert reply is not None
    assert "Juntei as lições" in reply.text
    assert "```json" not in reply.text
    assert reply.lessons is not None
    assert len(reply.lessons) == 1
    assert reply.lessons[0].chapter_seqs == [1, 2, 3, 4]


def test_stream_chat_without_plan_block_extracts_text_only() -> None:
    """Texto sem bloco JSON → extract_planner_reply devolve texto sem lessons."""
    from kubo.study.planner import extract_planner_reply

    reply = extract_planner_reply("Entendi, vou manter o plano.", _chapters())
    assert reply is not None
    assert reply.text == "Entendi, vou manter o plano."
    assert reply.lessons is None


def test_stream_chat_incoherent_plan_discarded() -> None:
    """Bloco JSON com seq inexistente → lessons descartado, texto preservado."""
    from kubo.study.planner import extract_planner_reply

    full_text = (
        'Aqui está.\n\n```json\n{"lessons": [{"title": "Fantasma", "chapter_seqs": [99]}]}\n```'
    )
    reply = extract_planner_reply(full_text, _chapters())
    assert reply is not None
    assert "Aqui está." in reply.text
    assert reply.lessons is None
