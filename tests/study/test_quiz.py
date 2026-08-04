"""KUBO-201 — Correção do quiz: domínio puro (sem banco, sem rede).

O quiz vive na lição como lista de objetos flexíveis (o schema é FLEXIBLE porque
o formato vem da persona `tutor`). Corrigir é comparar a resposta do dono com o
`answer_index` de cada questão — e o resultado é o que alimenta a recapitulação
da lição seguinte (ADR-0049 §II).

Entrada hostil dos dois lados: as respostas vêm de um form (dono pode mandar
qualquer coisa) e o quiz nasceu de um arquivo enviado (`answer_index` pode estar
fora da faixa mesmo depois da validação do tutor, se a lição foi gravada por uma
versão anterior).
"""

from __future__ import annotations

import pytest

from kubo.errors import QuizAnswerError
from kubo.study.quiz import grade


def _quiz() -> list[dict[str, object]]:
    return [
        {
            "question": "O que é um Tema?",
            "options": ["Um container", "Um arquivo"],
            "answer_index": 0,
            "explanation": "Tema é o container do estudo.",
        },
        {
            "question": "O que é uma Seção?",
            "options": ["Um capítulo", "Uma divisão tópica", "Um plano"],
            "answer_index": 1,
            "explanation": "Seção é a divisão tópica do capítulo.",
        },
    ]


def test_all_correct() -> None:
    """Todas certas: contagem cheia e nenhuma questão errada."""
    result = grade(_quiz(), [0, 1])
    assert result.correct_count == 2
    assert result.wrong_questions == []


def test_partial_reports_wrong_question_text() -> None:
    """A questão errada volta pelo ENUNCIADO — é o que o tutor recapitula."""
    result = grade(_quiz(), [1, 1])
    assert result.correct_count == 1
    assert result.wrong_questions == ["O que é um Tema?"]


def test_all_wrong() -> None:
    """Todas erradas: contagem zero e as duas questões na lista."""
    result = grade(_quiz(), [1, 0])
    assert result.correct_count == 0
    assert len(result.wrong_questions) == 2


def test_answer_count_must_match_quiz() -> None:
    """Responder menos (ou mais) que o número de questões é recusado.

    Aceitar um envio parcial gravaria um desempenho que não corresponde ao quiz —
    e o registro é único por lição, então não haveria segunda chance de corrigir.
    """
    with pytest.raises(QuizAnswerError):
        grade(_quiz(), [0])
    with pytest.raises(QuizAnswerError):
        grade(_quiz(), [0, 1, 1])


def test_answer_out_of_range_is_rejected() -> None:
    """Índice fora das alternativas é recusado, não tratado como erro do dono."""
    with pytest.raises(QuizAnswerError):
        grade(_quiz(), [0, 9])


def test_empty_quiz_is_rejected() -> None:
    """Lição placeholder (quiz vazio) não pode ser concluída."""
    with pytest.raises(QuizAnswerError):
        grade([], [])


def test_malformed_answer_index_is_rejected() -> None:
    """`answer_index` ausente ou fora da faixa vinda do banco não vira 500.

    O quiz é objeto FLEXIBLE no schema: uma lição gravada por versão anterior
    pode não ter o campo. A recusa é explícita (o dono vê a lição como
    inconsultável) em vez de KeyError.
    """
    quiz: list[dict[str, object]] = [{"question": "Sem gabarito", "options": ["a", "b"]}]
    with pytest.raises(QuizAnswerError):
        grade(quiz, [0])
