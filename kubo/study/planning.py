"""Cadência de estudo (ADR-0043, KUBO-136): quando o plano termina.

Domínio PURO — `date` sem timezone, sem banco, sem LLM. A data-alvo é derivada
(cadência + número de lições), nunca digitada pelo dono: mudar a cadência ou
remover uma lição recalcula o alvo pelo mesmo caminho.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, timedelta

# Dias da semana aceitos, na ordem de `date.weekday()` (0 = segunda).
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def normalized_weekdays(weekdays: Collection[str]) -> set[int]:
    """Converte os dias habilitados em índices de `date.weekday()`, sem repetição.

    `weekdays` é um CONJUNTO: ordem e repetição vindas de um formulário não contam.
    Dia fora do vocabulário é ValueError — nunca ignorado em silêncio, senão uma
    cadência digitada errado viraria um plano com menos dias do que o dono pediu.
    """
    unknown = sorted({day for day in weekdays if day not in _WEEKDAYS})
    if unknown:
        raise ValueError(f"dia da semana inválido: {', '.join(unknown)}")
    enabled = {_WEEKDAYS.index(day) for day in weekdays}
    if not enabled:
        raise ValueError("a cadência precisa de ao menos um dia da semana")
    return enabled


def next_study_day(*, after: date, weekdays: Collection[str]) -> date:
    """Próximo dia habilitado ESTRITAMENTE depois de `after`.

    Usado pelo job da véspera (KUBO-137): a lição gerada hoje é a do PRÓXIMO dia de
    estudo, então `after` (hoje) nunca conta como resposta, mesmo sendo habilitado.
    `weekdays` inválido/vazio é ValueError, pela mesma validação de
    `normalized_weekdays`.
    """
    enabled = normalized_weekdays(weekdays)
    # Distância a partir do dia SEGUINTE a `after` (por isso o `- 1` antes do módulo e
    # o `+ 1` depois): a menor delas é o próximo dia habilitado, e o próprio `after`
    # nunca entra na conta.
    ahead = min((day - after.weekday() - 1) % 7 for day in enabled) + 1
    return after + timedelta(days=ahead)


def compute_target_date(*, start: date, weekdays: Collection[str], lesson_count: int) -> date:
    """Data da última lição: a `lesson_count`-ésima ocorrência de um dia habilitado.

    Conta a partir de `start` INCLUSIVE — se `start` já é um dia habilitado, ele é a
    primeira ocorrência. `weekdays` vazio/inválido ou `lesson_count < 1` é ValueError:
    um plano sem dia habilitado nunca terminaria, e devolver uma data qualquer
    esconderia o erro dentro do plano salvo.
    """
    if lesson_count < 1:
        raise ValueError("um plano precisa de ao menos uma lição")
    enabled = normalized_weekdays(weekdays)
    # Distância em dias de `start` até cada dia habilitado da PRIMEIRA semana (0 = o
    # próprio `start`). Com elas, a n-ésima ocorrência é aritmética direta: semanas
    # inteiras + a posição dentro da semana — sem varrer dia a dia.
    offsets = sorted((day - start.weekday()) % 7 for day in enabled)
    weeks, position = divmod(lesson_count - 1, len(offsets))
    return start + timedelta(days=weeks * 7 + offsets[position])
