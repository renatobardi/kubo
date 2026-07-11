"""Seam do executor de LLM — abstração que workers dependem (ADR-0013 §III/§IV).

`Executor` é o Protocol que workers de destilação/chat recebem no construtor:
satisfeito por `ApiExecutor` (kubo/executors/api.py) em produção e por fakes
em teste. Nenhum worker importa `ApiExecutor` diretamente — dependeria da
implementação concreta (LiteLLM) em vez do seam, quebrando a troca por fake
nos testes unitários (CLAUDE.md: "LLMs em testes sempre mockados").
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Executor(Protocol):
    """Invoca um LLM com instrução + conteúdo não confiável, devolve saída tipada.

    Espelha a assinatura de `ApiExecutor.complete` (ADR-0013 §IV): a demarcação
    de `untrusted_content` como dado (nunca instrução) e a validação contra
    `response_model` são responsabilidade de quem implementa o Protocol, nunca
    do worker chamador.
    """

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        """Invoca o LLM e devolve a resposta validada contra `response_model`."""
        ...
