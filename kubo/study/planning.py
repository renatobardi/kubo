"""Cadência de estudo (ADR-0043, KUBO-136): quando o plano termina.

Domínio PURO — `date` sem timezone, sem banco, sem LLM. A data-alvo é derivada
(cadência + número de lições), nunca digitada pelo dono: mudar a cadência ou
remover uma lição recalcula o alvo pelo mesmo caminho.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

# Dias da semana aceitos, na ordem de `date.weekday()` (0 = segunda).
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def compute_target_date(*, start: date, weekdays: Collection[str], lesson_count: int) -> date:
    """Data da última lição: a `lesson_count`-ésima ocorrência de um dia habilitado.

    Conta a partir de `start` INCLUSIVE — se `start` já é um dia habilitado, ele é a
    primeira ocorrência. `weekdays` vazio/inválido ou `lesson_count < 1` é ValueError:
    um plano sem dia habilitado nunca terminaria, e devolver uma data qualquer
    esconderia o erro dentro do plano salvo.
    """
    raise NotImplementedError
