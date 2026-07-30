"""Cadência de estudo (ADR-0043, KUBO-136): data-alvo derivada do plano.

Unit puro — sem banco, sem LLM, sem relógio. TODA data de entrada traz a âncora do
dia da semana (`assert start.weekday() == ...`): sem isso o teste depende de
aritmética de calendário na cabeça de quem lê, e um erro de anotação viraria um
"comportamento" fixado errado.
"""

from __future__ import annotations

from datetime import date

import pytest

from kubo.study.planning import compute_target_date, next_study_day

_WEEK = ["mon", "tue", "wed", "thu", "fri"]


def test_single_lesson_starting_on_enabled_day_is_the_start_itself() -> None:
    """`start` habilitado conta como a PRIMEIRA ocorrência — 1 lição termina hoje."""
    start = date(2026, 8, 3)
    assert start.weekday() == 0  # segunda

    assert compute_target_date(start=start, weekdays=_WEEK, lesson_count=1) == start


def test_counts_enabled_days_across_the_weekend() -> None:
    """Seg-sex a partir de uma quarta: 4 lições caem na segunda seguinte, não na sexta."""
    start = date(2026, 8, 5)
    assert start.weekday() == 2  # quarta

    target = compute_target_date(start=start, weekdays=_WEEK, lesson_count=4)

    assert target == date(2026, 8, 10)  # qua, qui, sex, SEG


def test_start_on_disabled_day_waits_for_the_next_enabled_one() -> None:
    """Começar num sábado com cadência seg-sex adia a primeira lição para segunda."""
    start = date(2026, 8, 8)
    assert start.weekday() == 5  # sábado

    assert compute_target_date(start=start, weekdays=_WEEK, lesson_count=1) == date(2026, 8, 10)


def test_single_weekday_advances_one_week_per_lesson() -> None:
    """Cadência de um dia só: cada lição salta uma semana inteira."""
    start = date(2026, 8, 3)
    assert start.weekday() == 0  # segunda

    target = compute_target_date(start=start, weekdays=["sun"], lesson_count=3)

    assert target == date(2026, 8, 23)  # 09, 16, 23 — domingos


def test_large_count_crosses_several_weeks() -> None:
    """Plano longo atravessa semanas sem acumular erro de contagem."""
    start = date(2026, 8, 3)
    assert start.weekday() == 0  # segunda

    target = compute_target_date(start=start, weekdays=["mon", "wed", "fri"], lesson_count=10)

    assert target == date(2026, 8, 24)


def test_weekday_order_and_repetition_do_not_change_the_result() -> None:
    """`weekdays` é um CONJUNTO de dias: ordem e repetição vindas do form não contam."""
    start = date(2026, 8, 3)
    assert start.weekday() == 0  # segunda

    scrambled = compute_target_date(
        start=start, weekdays=["fri", "mon", "mon", "wed"], lesson_count=10
    )

    assert scrambled == compute_target_date(
        start=start, weekdays=["mon", "wed", "fri"], lesson_count=10
    )


def test_empty_weekdays_is_an_error() -> None:
    """Sem dia habilitado o plano nunca terminaria — falha alto, não devolve data."""
    with pytest.raises(ValueError):
        compute_target_date(start=date(2026, 8, 3), weekdays=[], lesson_count=3)


def test_unknown_weekday_is_an_error() -> None:
    """Dia fora do vocabulário (ex.: 'monday') é erro — não é ignorado em silêncio."""
    with pytest.raises(ValueError):
        compute_target_date(start=date(2026, 8, 3), weekdays=["monday"], lesson_count=3)


@pytest.mark.parametrize("count", [0, -1])
def test_non_positive_lesson_count_is_an_error(count: int) -> None:
    """Plano sem lição não tem data-alvo; devolver `start` esconderia o erro."""
    with pytest.raises(ValueError):
        compute_target_date(start=date(2026, 8, 3), weekdays=_WEEK, lesson_count=count)


# --- next_study_day (KUBO-137): o dia que o job da véspera prepara ---------------------
#
# A pergunta é outra: `compute_target_date` conta ocorrências a partir de `start`
# INCLUSIVE; aqui o dia de referência é a VÉSPERA, e por isso ele nunca é a resposta.


def test_next_study_day_skips_the_reference_day_itself() -> None:
    """Rodando na véspera, o dia habilitado de HOJE não conta — a lição é a de amanhã."""
    today = date(2026, 8, 5)
    assert today.weekday() == 2  # quarta, habilitada em _WEEK

    assert next_study_day(after=today, weekdays=_WEEK) == date(2026, 8, 6)  # quinta


def test_next_study_day_jumps_the_weekend() -> None:
    """Sexta à noite com cadência seg-sex prepara a lição de SEGUNDA, não de sábado."""
    friday = date(2026, 8, 7)
    assert friday.weekday() == 4

    assert next_study_day(after=friday, weekdays=_WEEK) == date(2026, 8, 10)  # segunda


def test_next_study_day_from_a_disabled_day_finds_the_next_enabled_one() -> None:
    """Véspera num dia sem estudo (sábado) ainda aponta para o próximo dia habilitado."""
    saturday = date(2026, 8, 8)
    assert saturday.weekday() == 5

    assert next_study_day(after=saturday, weekdays=_WEEK) == date(2026, 8, 10)


def test_next_study_day_with_a_single_weekday_advances_a_full_week() -> None:
    """Cadência de um dia só: a partir do próprio dia, o próximo é uma semana depois."""
    sunday = date(2026, 8, 9)
    assert sunday.weekday() == 6

    assert next_study_day(after=sunday, weekdays=["sun"]) == date(2026, 8, 16)


@pytest.mark.parametrize("weekdays", [[], ["monday"]])
def test_next_study_day_rejects_invalid_cadence(weekdays: list[str]) -> None:
    """Cadência vazia ou com dia inventado é erro — nunca uma data qualquer."""
    with pytest.raises(ValueError):
        next_study_day(after=date(2026, 8, 5), weekdays=weekdays)
