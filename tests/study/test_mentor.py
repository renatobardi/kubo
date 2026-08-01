"""KUBO-163 — Mentor: chat conversacional na Fase 1 (draft).

Molde do Tutor/Planner: classe fina sobre um executor, sem flow e sem banco.
A diferença é o streaming: o mentor devolve um iterator de chunks de texto
(resposta surgindo aos poucos), não um objeto estruturado de uma vez.

De que lado cada dado viaja:
- `untrusted_content` leva os sumários dos Materiais + histórico da conversa
  (nasceu do arquivo enviado e das mensagens anteriores);
- a instrução leva o prompt da persona + work_context (do sistema/dono).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from kubo.study.mentor import Mentor, extract_reply


class _FakeStreamingExecutor:
    """Fake que satisfaz o protocolo StreamingExecutor para testes."""

    def __init__(self, chunks: Sequence[str]) -> None:
        self._chunks = chunks
        self.last_instruction: str = ""
        self.last_content: str = ""

    def stream(self, instruction: str, untrusted_content: str) -> Iterator[str]:
        self.last_instruction = instruction
        self.last_content = untrusted_content
        for chunk in self._chunks:
            yield chunk


def _material_summaries() -> list[str]:
    """Sumários de Materiais do Tema (não conteúdo completo)."""
    return ["Guia sobre agentes e ateliê pessoal.", "Artigo sobre transformação agêntica."]


def _test_prompt() -> str:
    return "Você é o mentor do ateliê Kubo."


def _chat(
    executor: _FakeStreamingExecutor,
    *,
    user_message: str = "Teste",
    material_summaries: Sequence[str] | None = None,
    history: Sequence[tuple[str, str]] = (),
    work_context: str = "",
) -> str:
    """Helper: roda stream_chat e devolve o texto completo (concatenado)."""
    mentor = Mentor(executor=executor, prompt=_test_prompt())  # type: ignore[arg-type]
    return "".join(
        mentor.stream_chat(
            user_message=user_message,
            material_summaries=material_summaries or _material_summaries(),
            history=history,
            work_context=work_context,
        )
    )


# --- stream chat ------------------------------------------------------------------------


def test_mentor_stream_devolves_chunks() -> None:
    """Mentor devolve um iterator que produz os chunks do executor."""
    executor = _FakeStreamingExecutor(["Olá! ", "Posso ajudar ", "com o estudo."])
    full = _chat(executor, user_message="Quero estudar agentic coding.")
    reply = extract_reply(full)
    assert reply.text == "Olá! Posso ajudar com o estudo."
    assert reply.suggested_name is None
    assert reply.suggested_focus is None
    assert reply.suggested_depth is None


def test_mentor_stream_passes_work_context_in_instruction() -> None:
    """work_context viaja na instrução (lado do sistema/dono), não no untrusted_content."""
    executor = _FakeStreamingExecutor(["Resposta."])
    _chat(
        executor,
        user_message="Olá",
        work_context="Engenheiro de software sênior, trabalha com Python.",
    )
    assert "Engenheiro de software sênior" in executor.last_instruction
    # work_context NÃO vai no untrusted_content
    assert "Engenheiro de software sênior" not in executor.last_content


def test_mentor_stream_passes_summaries_in_untrusted_content() -> None:
    """Sumários dos Materiais viajam como untrusted_content (cercado pelo executor)."""
    executor = _FakeStreamingExecutor(["Resposta."])
    _chat(executor, user_message="O que achas?")
    assert "Guia sobre agentes" in executor.last_content
    assert "Artigo sobre transformação" in executor.last_content


def test_mentor_stream_passes_history_in_untrusted_content() -> None:
    """Histórico da conversa viaja como untrusted_content (janela deslizante)."""
    executor = _FakeStreamingExecutor(["Resposta."])
    history = [
        ("user", "Quero focar em agentes."),
        ("assistant", "Bom foco! Qual sua experiência?"),
    ]
    _chat(executor, user_message="Trabalho com Python há 10 anos.", history=history)
    assert "Quero focar em agentes." in executor.last_content
    assert "Bom foco! Qual sua experiência?" in executor.last_content
    assert "Trabalho com Python há 10 anos." in executor.last_content


# --- extração de sugestões (nome, foco, profundidade) -----------------------------------


def test_mentor_extracts_suggested_name() -> None:
    """Sugestão de nome entre colchetes [Sugestão: Nome] é extraída da resposta."""
    executor = _FakeStreamingExecutor(["Ótimo! ", "[Sugestão: Agentes e Ateliê Pessoal]"])
    full = _chat(executor, user_message="Quero um nome para o estudo.")
    reply = extract_reply(full)
    assert reply.suggested_name == "Agentes e Ateliê Pessoal"
    # O texto NÃO contém a marcação de sugestão (foi limpa)
    assert "[Sugestão:" not in reply.text


def test_mentor_extracts_suggested_focus() -> None:
    """Sugestão de foco [Foco: ...] é extraída da resposta."""
    executor = _FakeStreamingExecutor(
        ["Vejo que quer focar em ", "[Foco: Sistemas agênticos com Python]"]
    )
    full = _chat(executor, user_message="Foco em agentes.")
    reply = extract_reply(full)
    assert reply.suggested_focus == "Sistemas agênticos com Python"
    assert "[Foco:" not in reply.text


def test_mentor_extracts_suggested_depth() -> None:
    """Sugestão de profundidade [Profundidade: ...] é extraída da resposta."""
    executor = _FakeStreamingExecutor(["Para seu nível, ", "[Profundidade: aprofundado]"])
    full = _chat(executor, user_message="Quero ir fundo.")
    reply = extract_reply(full)
    assert reply.suggested_depth == "aprofundado"
    assert "[Profundidade:" not in reply.text


def test_mentor_no_suggestion_returns_none() -> None:
    """Resposta sem marcações de sugestão devolve None em todos os campos."""
    executor = _FakeStreamingExecutor(["Só conversando, sem sugerir nada agora."])
    full = _chat(executor, user_message="Oi")
    reply = extract_reply(full)
    assert reply.suggested_name is None
    assert reply.suggested_focus is None
    assert reply.suggested_depth is None


def test_mentor_multiple_suggestions_in_one_reply() -> None:
    """Múltiplas sugestões numa resposta são todas extraídas."""
    executor = _FakeStreamingExecutor(
        [
            "Bom! ",
            "[Sugestão: Agentes em Python] ",
            "[Foco: Sistemas agênticos] ",
            "[Profundidade: intermediario]",
        ]
    )
    full = _chat(executor, user_message="Monta tudo.")
    reply = extract_reply(full)
    assert reply.suggested_name == "Agentes em Python"
    assert reply.suggested_focus == "Sistemas agênticos"
    assert reply.suggested_depth == "intermediario"


def test_mentor_depth_invalid_value_ignored() -> None:
    """Profundidade fora dos valores válidos é ignorada (None)."""
    executor = _FakeStreamingExecutor(["[Profundidade: absurdo]"])
    full = _chat(executor, user_message="Teste")
    reply = extract_reply(full)
    assert reply.suggested_depth is None
