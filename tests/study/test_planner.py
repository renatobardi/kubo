"""Persona `planner` (ADR-0043, KUBO-136/KUBO-185): seções → lições.

Unit puro: o executor é um `_FakeExecutor` (molde de tests/workers/test_distiller.py) —
nenhum teste aqui toca LiteLLM ou rede (CLAUDE.md: "LLMs em testes sempre mockados").

Comportamento fixado: o LLM propõe, o CÓDIGO confere. Uma proposta que não bate com a
estrutura de seções do material (par inexistente, repetido, fora de ordem ou com
lacunas) é descartada inteira — salvar meia proposta daria ao dono um plano que aponta
para seções que ele não tem.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel, ValidationError
from surrealdb import RecordID

from kubo.errors import ExecutorError, MalformedOutputError, RateLimitExhausted
from kubo.store.study import MaterialSection
from kubo.study.planner import (
    PlanLesson,
    Planner,
    PlannerChatReply,
    PlanProposal,
    mechanical_proposal,
)

T = TypeVar("T", bound=BaseModel)

_PROMPT = "agrupe as seções em lições"


class _FakeExecutor:
    """Fake de `Executor`: devolve `output` ou levanta `error` na chamada, e registra o
    `untrusted_content` recebido — usado para provar que a estrutura chega ao LLM."""

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


def _sections(chapters: int = 2, per_chapter: int = 2) -> list[MaterialSection]:
    """Seções do material: chapter_seq global (1-based), section_seq local (1-based)."""
    result: list[MaterialSection] = []
    for ch_seq in range(1, chapters + 1):
        for s_seq in range(1, per_chapter + 1):
            idx = (ch_seq - 1) * per_chapter + s_seq
            result.append(
                MaterialSection(
                    id=RecordID("material_section", f"s{idx}"),
                    material=RecordID("material", "m1"),
                    material_chapter=RecordID("material_chapter", f"c{ch_seq}"),
                    seq=s_seq,
                    title=f"Seção {ch_seq}.{s_seq}",
                    anchor_text="",
                    content="",
                    summary=f"Resumo da seção {ch_seq}.{s_seq}.",
                    chapter_seq=ch_seq,
                )
            )
    return result


def _proposal(*lessons: tuple[str, list[tuple[int, int]]]) -> PlanProposal:
    return PlanProposal(lessons=[PlanLesson(title=t, sections=seqs) for t, seqs in lessons])


def test_proposal_rejects_unknown_fields() -> None:
    """`extra="forbid"`: campo inventado pelo LLM não entra no modelo."""
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(
            {"lessons": [{"title": "Aula 1", "sections": [[1, 1]]}], "notes": "oi"}
        )


def test_lesson_requires_at_least_one_section() -> None:
    """Lição sem seção não é lição — o modelo recusa antes de virar plano."""
    with pytest.raises(ValidationError):
        PlanLesson(title="Aula vazia", sections=[])


def test_propose_returns_validated_proposal() -> None:
    """Caminho feliz: a proposta coerente volta como veio, com a estrutura no prompt."""
    executor = _FakeExecutor(
        output=_proposal(("Fundamentos", [(1, 1), (1, 2)]), ("Prática", [(2, 1), (2, 2)]))
    )
    planner = Planner(executor=executor, prompt=_PROMPT)

    proposal = planner.propose(_sections())

    assert proposal is not None
    assert [lesson.title for lesson in proposal.lessons] == ["Fundamentos", "Prática"]
    assert [lesson.sections for lesson in proposal.lessons] == [
        [(1, 1), (1, 2)],
        [(2, 1), (2, 2)],
    ]
    assert "1.1 Seção 1.1" in executor.received_content[0]


def test_propose_rejects_section_that_does_not_exist() -> None:
    """Par fora do material invalida a proposta INTEIRA — nada de plano parcial."""
    executor = _FakeExecutor(
        output=_proposal(("Fundamentos", [(1, 1), (1, 2)]), ("Fantasma", [(9, 9)]))
    )
    planner = Planner(executor=executor, prompt=_PROMPT)

    assert planner.propose(_sections()) is None


def test_propose_rejects_section_repeated_across_lessons() -> None:
    """Seção em duas lições é proposta incoerente: o dono estudaria o mesmo duas vezes."""
    executor = _FakeExecutor(
        output=_proposal(("Fundamentos", [(1, 1), (1, 2)]), ("Revisão", [(1, 2), (2, 1)]))
    )
    planner = Planner(executor=executor, prompt=_PROMPT)

    assert planner.propose(_sections()) is None


def test_propose_rejects_lesson_with_sections_out_of_order() -> None:
    """Dentro da lição a ordem de leitura tem que ser crescente — o livro tem ordem."""
    executor = _FakeExecutor(output=_proposal(("Bagunça", [(2, 1), (1, 1)])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    assert planner.propose(_sections()) is None


def test_propose_accepts_partial_coverage() -> None:
    """Proposta que omite seções é válida — o planner pode focar em partes do material."""
    executor = _FakeExecutor(
        output=_proposal(("Fundamentos", [(1, 1)]), ("Prática", [(2, 1), (2, 2)]))
    )
    planner = Planner(executor=executor, prompt=_PROMPT)

    proposal = planner.propose(_sections())
    assert proposal is not None
    assert len(proposal.lessons) == 2


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

    assert planner.propose(_sections()) is None


def test_mechanical_proposal_is_one_lesson_per_section_in_order() -> None:
    """Fallback determinístico: 1 seção = 1 lição, título e ordem da seção."""
    sections = _sections(2, 2)

    proposal = mechanical_proposal(sections)

    assert [lesson.sections for lesson in proposal.lessons] == [
        [(1, 1)],
        [(1, 2)],
        [(2, 1)],
        [(2, 2)],
    ]
    assert [lesson.title for lesson in proposal.lessons] == [
        "Seção 1.1",
        "Seção 1.2",
        "Seção 2.1",
        "Seção 2.2",
    ]


def test_mechanical_proposal_respects_section_seq_not_list_position() -> None:
    """A ordem vem do (chapter_seq, seq), não da posição na lista devolvida pela store."""
    sections: Sequence[MaterialSection] = list(reversed(_sections(2, 2)))

    proposal = mechanical_proposal(sections)

    assert [lesson.sections for lesson in proposal.lessons] == [
        [(1, 1)],
        [(1, 2)],
        [(2, 1)],
        [(2, 2)],
    ]


def test_mechanical_proposal_raises_on_empty_sections() -> None:
    """Lista vazia levanta ValueError — quem chama deve guardar antes."""
    with pytest.raises(ValueError, match="pelo menos 1 seção"):
        mechanical_proposal([])


def test_mechanical_proposal_groups_when_exceeding_max_lessons() -> None:
    """Com >200 seções, agrupa em ≤200 lições para respeitar o teto do modelo."""
    sections = _sections(101, 2)  # 202 seções → 2 grupos de 101

    proposal = mechanical_proposal(sections)

    assert len(proposal.lessons) <= 200
    # Todas as seções estão cobertas.
    all_pairs = [pair for lesson in proposal.lessons for pair in lesson.sections]
    assert len(all_pairs) == 202


def test_summary_is_capped_before_reaching_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """O prefixo (focus/depth/transcript) é truncado, mas o bloco de seções NUNCA.

    Os títulos vêm do arquivo que o dono enviou e nada os limita antes daqui: sem o teto,
    um material com milhares de seções faria o custo da proposta refém do upload. O teto
    truncava o prompt inteiro; KUBO-165 mudou para truncar só o prefixo, preservando o
    bloco de seções (AC8 — sem ele o planner não tem o que agrupar).
    """
    cap = 100
    monkeypatch.setattr("kubo.study.planner._MAX_SUMMARY_TEXT", cap)
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [(1, 1), (1, 2)])))

    Planner(executor=executor, prompt=_PROMPT).propose(_sections(40, 5))

    content = executor.received_content[0]
    # O bloco de seções é preservado integralmente (pode exceder o cap).
    assert "40.5 Seção 40.5" in content
    # Mas o prefixo (focus/depth/transcript) foi truncado — só há seções.
    assert "Foco" not in content


def test_a_short_summary_is_not_truncated() -> None:
    """Controle do teste acima: abaixo do teto, a estrutura chega inteira."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [(1, 1), (1, 2)])))

    Planner(executor=executor, prompt=_PROMPT).propose(_sections(2, 2))

    assert "2.2 Seção 2.2" in executor.received_content[0]


# --- KUBO-164: novo input (campos + resumo do mentor + sumários) -------------------------


def test_propose_includes_focus_and_depth_in_prompt() -> None:
    """Focus e depth do Tema chegam ao prompt do planner (ADR-0047 §2)."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [(1, 1), (1, 2)])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _sections(),
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
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [(1, 1), (1, 2)])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _sections(),
        focus=None,
        depth=None,
        mentor_transcript="Dono: Quero focar em agentes. Mentor: Vou sugerir um plano prático.",
        material_summaries=[],
    )

    content = executor.received_content[0]
    assert "Quero focar em agentes" in content


def test_propose_includes_material_summaries_in_prompt() -> None:
    """Os sumários dos materiais chegam ao prompt do planner."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [(1, 1), (1, 2)])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _sections(),
        focus=None,
        depth=None,
        mentor_transcript="",
        material_summaries=["Um guia sobre agentes.", "Livro avançado de LLMs."],
    )

    content = executor.received_content[0]
    assert "Um guia sobre agentes." in content
    assert "Livro avançado de LLMs." in content


def test_propose_includes_section_summaries_in_prompt() -> None:
    """KUBO-185: o sumário de cada seção chega ao prompt do planner."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [(1, 1), (1, 2)])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(_sections())

    content = executor.received_content[0]
    assert "Resumo da seção 1.1." in content
    assert "Resumo da seção 1.2." in content


def test_propose_includes_planning_history_in_prompt() -> None:
    """KUBO-165: a conversa da Fase 2 chega ao prompt do planner no repropose."""
    executor = _FakeExecutor(output=_proposal(("Fundamentos", [(1, 1), (1, 2)])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.propose(
        _sections(),
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
    executor = _FakeExecutor(
        output=_proposal(("Fundamentos", [(1, 1), (1, 2)]), ("Prática", [(2, 1), (2, 2)]))
    )
    planner = Planner(executor=executor, prompt=_PROMPT)

    proposal = planner.propose(_sections())

    assert proposal is not None
    assert "1.1 Seção 1.1" in executor.received_content[0]


# --- KUBO-165: chat com planner (Fase 2 — ajuste incremental) ---------------------------


def _chat_reply(text: str, *lessons: tuple[str, list[tuple[int, int]]]) -> PlannerChatReply:
    return PlannerChatReply(
        text=text,
        lessons=[PlanLesson(title=t, sections=s) for t, s in lessons] or None,
    )


def test_chat_returns_text_and_updated_plan() -> None:
    """Chat com planner devolve texto + plano atualizado coerente."""
    executor = _FakeExecutor(
        output=_chat_reply(
            "Juntei as lições 1 e 2.",
            ("Fundamentos", [(1, 1), (1, 2), (2, 1), (2, 2)]),
        )
    )
    planner = Planner(executor=executor, prompt=_PROMPT)

    reply = planner.chat(
        user_message="junta lição 1 e 2",
        sections=_sections(),
        current_plan=[("Fundamentos", [(1, 1), (1, 2)]), ("Prática", [(2, 1), (2, 2)])],
    )

    assert reply is not None
    assert reply.text == "Juntei as lições 1 e 2."
    assert reply.lessons is not None
    assert len(reply.lessons) == 1
    assert reply.lessons[0].sections == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_chat_without_plan_update_returns_text_only() -> None:
    """Mensagem que não toca o plano devolve texto sem lessons."""
    executor = _FakeExecutor(output=_chat_reply("Entendi, vou manter o plano."))
    planner = Planner(executor=executor, prompt=_PROMPT)

    reply = planner.chat(
        user_message="o que acha do plano?",
        sections=_sections(),
        current_plan=[("Fundamentos", [(1, 1), (1, 2)])],
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
        sections=_sections(),
        current_plan=[("Fundamentos", [(1, 1), (1, 2)]), ("Prática", [(2, 1), (2, 2)])],
    )

    content = executor.received_content[0]
    assert "Fundamentos" in content
    assert "Prática" in content
    assert "1.1" in content


def test_chat_includes_planning_history_in_prompt() -> None:
    """O histórico da conversa com planner chega ao prompt."""
    executor = _FakeExecutor(output=_chat_reply("Ok."))
    planner = Planner(executor=executor, prompt=_PROMPT)

    planner.chat(
        user_message="muda de novo",
        sections=_sections(),
        current_plan=[("L1", [(1, 1)])],
        planning_history=[("user", "junta tudo"), ("assistant", "ok, juntei")],
    )

    content = executor.received_content[0]
    assert "junta tudo" in content


def test_chat_rejects_incoherent_plan_update() -> None:
    """Plano atualizado com par inexistente é descartado — lessons vira None."""
    executor = _FakeExecutor(output=_chat_reply("Aqui está.", ("Fantasma", [(9, 9)])))
    planner = Planner(executor=executor, prompt=_PROMPT)

    reply = planner.chat(
        user_message="adiciona seção 9.9",
        sections=_sections(),
        current_plan=[("L1", [(1, 1)])],
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
            sections=_sections(),
            current_plan=[("L1", [(1, 1)])],
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

    def stream(self, instruction: str, untrusted_content: str) -> Iterator[str]:
        self.received_instructions.append(instruction)
        self.received_content.append(untrusted_content)
        if self._error is not None:
            raise self._error
        yield from self._chunks


def test_stream_chat_yields_text_chunks() -> None:
    """stream_chat devolve chunks de texto do LLM (streaming)."""
    from kubo.study.planner import Planner

    stream_executor = _FakeStreamingExecutor(chunks=["Juntei ", "as lições."])
    planner = Planner(executor=_FakeExecutor(), prompt=_PROMPT)

    chunks = list(
        planner.stream_chat(
            stream_executor,
            user_message="junta lição 1 e 2",
            sections=_sections(),
            current_plan=[("L1", [(1, 1), (1, 2)])],
        )
    )
    assert chunks == ["Juntei ", "as lições."]


def test_stream_chat_with_plan_block_extracts_reply() -> None:
    """stream_chat com bloco JSON no final → extract_planner_reply devolve texto + plano."""
    from kubo.study.planner import extract_planner_reply

    full_text = (
        "Juntei as lições 1 e 2 numa só.\n\n"
        '```json\n{"lessons": [{"title": "Fundamentos", "sections": '
        "[[1, 1], [1, 2], [2, 1], [2, 2]]}]}\n```"
    )
    reply = extract_planner_reply(full_text, _sections())
    assert reply is not None
    assert "Juntei as lições" in reply.text
    assert "```json" not in reply.text
    assert reply.lessons is not None
    assert len(reply.lessons) == 1
    assert reply.lessons[0].sections == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_stream_chat_without_plan_block_extracts_text_only() -> None:
    """Texto sem bloco JSON → extract_planner_reply devolve texto sem lessons."""
    from kubo.study.planner import extract_planner_reply

    reply = extract_planner_reply("Entendi, vou manter o plano.", _sections())
    assert reply is not None
    assert reply.text == "Entendi, vou manter o plano."
    assert reply.lessons is None


def test_stream_chat_incoherent_plan_discarded() -> None:
    """Bloco JSON com par inexistente → lessons descartado, texto preservado."""
    from kubo.study.planner import extract_planner_reply

    full_text = (
        'Aqui está.\n\n```json\n{"lessons": [{"title": "Fantasma", "sections": [[9, 9]]}]}\n```'
    )
    reply = extract_planner_reply(full_text, _sections())
    assert reply is not None
    assert "Aqui está." in reply.text
    assert reply.lessons is None


def test_stream_chat_propagates_executor_error() -> None:
    """stream_chat propaga ExecutorError durante a iteração dos chunks."""
    from kubo.errors import ExecutorError
    from kubo.study.planner import Planner

    stream_executor = _FakeStreamingExecutor(chunks=[], error=ExecutorError("provider down"))
    planner = Planner(executor=_FakeExecutor(), prompt=_PROMPT)

    with pytest.raises(ExecutorError):
        list(
            planner.stream_chat(
                stream_executor,
                user_message="junta",
                sections=_sections(),
                current_plan=[("L1", [(1, 1), (1, 2)])],
            )
        )
