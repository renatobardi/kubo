"""Correção do quiz da lição (KUBO-201, ADR-0049 §I/§II): domínio PURO.

Sem banco e sem rede: recebe o quiz como ele vive na lição (lista de objetos
FLEXIBLE, formato produzido pela persona `tutor`) e as respostas do dono, e
devolve quantas acertou + os ENUNCIADOS que errou. Os enunciados errados são o
que volta ao `tutor` como `misses` na geração da lição seguinte.

As duas entradas são hostis por motivos diferentes:
- as respostas vêm de um form (o dono pode mandar qualquer índice);
- o quiz nasceu de um arquivo enviado e o schema é FLEXIBLE, então uma lição
  gravada por versão anterior pode não ter `answer_index`.

Por isso a recusa é explícita (`QuizAnswerError`) em vez de KeyError ou de um
`correct_count` chutado: o registro de estudo é ÚNICO por lição, e um número
errado gravado aqui contamina a recapitulação sem chance de correção.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from kubo.errors import QuizAnswerError

_ANSWER_COUNT_MISMATCH = "Responda todas as questões do quiz antes de concluir a lição."
_ANSWER_OUT_OF_RANGE = "Resposta inválida: escolha uma das alternativas apresentadas."
_EMPTY_QUIZ = "Esta lição ainda não tem quiz — não é possível concluí-la."
_MALFORMED_QUIZ = "O quiz desta lição está inconsistente: gere a lição novamente."


@dataclass(frozen=True)
class QuizResult:
    """Resultado da correção: acertos e os enunciados errados (para a recapitulação)."""

    correct_count: int
    wrong_questions: list[str]


def grade(quiz: Sequence[Mapping[str, object]], answers: Sequence[int]) -> QuizResult:
    """Corrige as respostas do dono contra o gabarito do quiz.

    Levanta `QuizAnswerError` quando o quiz está vazio (lição placeholder), quando a
    contagem de respostas não casa com a de questões, quando uma resposta aponta para
    alternativa inexistente, ou quando a questão não traz gabarito utilizável.
    """
    if not quiz:
        raise QuizAnswerError(_EMPTY_QUIZ)
    if len(answers) != len(quiz):
        raise QuizAnswerError(_ANSWER_COUNT_MISMATCH)
    correct = 0
    wrong: list[str] = []
    for item, answer in zip(quiz, answers, strict=True):
        options = item.get("options")
        expected = item.get("answer_index")
        if not isinstance(options, Sequence) or isinstance(options, str) or not options:
            raise QuizAnswerError(_MALFORMED_QUIZ)
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise QuizAnswerError(_MALFORMED_QUIZ)
        if not 0 <= expected < len(options):
            raise QuizAnswerError(_MALFORMED_QUIZ)
        if not 0 <= answer < len(options):
            raise QuizAnswerError(_ANSWER_OUT_OF_RANGE)
        if answer == expected:
            correct += 1
        else:
            wrong.append(str(item.get("question", "")))
    return QuizResult(correct_count=correct, wrong_questions=wrong)
