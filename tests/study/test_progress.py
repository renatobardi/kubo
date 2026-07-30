"""Progresso do plano (ADR-0043, KUBO-138): a régua derivada de plano + estudos.

Unit puro — sem banco, sem LLM, sem relógio (`today` entra por parâmetro). TODA data
de entrada traz a âncora do dia da semana (`assert d.weekday() == ...`), no molde de
`test_planning.py`: sem isso o teste depende de aritmética de calendário na cabeça de
quem lê, e um erro de anotação viraria um "comportamento" fixado errado.

A pergunta central destes testes: a meta é DERIVAÇÃO, nunca entidade. Nada aqui lê ou
grava "meta batida" — atraso, streak e data projetada saem das mesmas datas que a UI
já tem.
"""

from __future__ import annotations

from datetime import date

import pytest

from kubo.study.progress import compute_progress

_WEEK = ["mon", "tue", "wed", "thu", "fri"]

# Ativação numa segunda; a semana inteira é dia de estudo.
_START = date(2026, 8, 3)
# Sexta da mesma semana: 5 dias de estudo decorridos contando a ativação.
_FRIDAY = date(2026, 8, 7)


def _days(*days: int) -> list[date]:
    """Datas de agosto/2026 como conclusões — uma entrada por registro de estudo."""
    return [date(2026, 8, day) for day in days]


def test_on_pace_has_no_delay_and_a_full_streak() -> None:
    """Uma lição por dia de estudo desde a ativação: nada atrasado, streak cheio."""
    assert _START.weekday() == 0  # segunda
    assert _FRIDAY.weekday() == 4  # sexta

    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 4, 5, 6, 7),
    )

    assert progress.done == 5
    assert progress.expected == 5  # seg..sex, ativação INCLUSIVE
    assert progress.behind == 0
    assert progress.streak == 5


def test_on_pace_projects_the_target_by_the_remaining_lessons() -> None:
    """No ritmo de uma lição por dia, o alvo é o N-ésimo dia de estudo à frente.

    5 lições restantes a partir de sexta caem em seg, ter, qua, qui, SEX da semana
    seguinte — o fim de semana não conta.
    """
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 4, 5, 6, 7),
    )

    assert progress.projected_target == date(2026, 8, 14)


def test_behind_counts_the_gap_between_expected_and_done() -> None:
    """Duas lições em cinco dias de estudo: o atraso é a diferença, não a sensação."""
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 4),
    )

    assert progress.expected == 5
    assert progress.done == 2
    assert progress.behind == 3


def test_behind_projects_a_later_target_from_the_real_pace() -> None:
    """A data projetada usa o ritmo REAL, não a cadência: 2/5 por dia estica o plano.

    8 lições restantes no ritmo de 0,4 por dia de estudo = 20 dias de estudo à frente
    de sexta 07/08 — quatro semanas cheias, 04/09. É esse número que a cutucada mostra;
    projetar pela cadência diria "14/08" e mentiria para o dono.
    """
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 4),
    )

    assert progress.projected_target == date(2026, 9, 4)


def test_two_lessons_in_one_day_put_the_plan_ahead() -> None:
    """`done` conta REGISTROS, não dias: quem estuda duas lições num dia fica adiantado.

    Adiantado nunca vira atraso negativo — `behind` é 0, e o alvo projetado se aproxima
    no máximo até as lições restantes (uma por dia de estudo).
    """
    wednesday = date(2026, 8, 5)
    assert wednesday.weekday() == 2

    progress = compute_progress(
        activated_on=_START,
        today=wednesday,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 3, 4, 5, 5),
    )

    assert progress.expected == 3
    assert progress.done == 5
    assert progress.behind == 0
    assert progress.projected_target == date(2026, 8, 12)  # 5 restantes: qui, sex, seg, ter, QUA


def test_streak_stops_at_the_first_missed_study_day() -> None:
    """Um buraco no meio zera o que veio antes: streak é o que está EM PÉ hoje."""
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 4, 6, 7),  # faltou a quarta (05)
    )

    assert progress.streak == 2  # quinta e sexta


def test_today_without_a_lesson_does_not_break_the_streak() -> None:
    """Hoje ainda não acabou: o dia corrente sem conclusão NÃO zera o streak.

    Sem esta regra, o painel mostraria "0 dias" toda manhã — e o streak viraria um
    número que só existe depois de estudar, inútil como incentivo.
    """
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 4, 5, 6),  # a sexta (hoje) ainda está pendente
    )

    assert progress.streak == 4


def test_weekend_does_not_break_the_streak() -> None:
    """Sábado e domingo não são dias de estudo: pular o fim de semana não conta falta."""
    monday = date(2026, 8, 10)
    assert monday.weekday() == 0

    progress = compute_progress(
        activated_on=_START,
        today=monday,
        weekdays=_WEEK,
        total=10,
        completions=_days(6, 7, 10),  # qui, sex, SEG — o fim de semana no meio
    )

    assert progress.streak == 3


def test_paused_days_are_discounted_from_what_is_expected() -> None:
    """Pausa CONGELA a régua: dia pausado não é dia esperado, logo não vira atraso.

    Sem o desconto, retomar um plano depois de duas semanas paradas chegaria com dez
    lições de atraso fabricadas — e a cutucada puniria o dono por ter usado a pausa.
    """
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3, 4, 5),
        paused_days=2,
    )

    assert progress.expected == 3  # 5 dias de estudo - 2 congelados
    assert progress.behind == 0


def test_paused_days_never_push_expected_below_zero() -> None:
    """Acumulador maior que os dias decorridos zera o esperado, não o vira negativo.

    Esperado negativo viraria "adiantado" falso e uma data projetada absurda.
    """
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=[],
        paused_days=99,
    )

    assert progress.expected == 0
    assert progress.behind == 0


def test_expected_never_passes_the_planned_lessons() -> None:
    """A régua para no fim do plano: um plano de 3 lições nunca fica 20 atrasado."""
    much_later = date(2026, 9, 30)
    assert much_later.weekday() == 2

    progress = compute_progress(
        activated_on=_START,
        today=much_later,
        weekdays=_WEEK,
        total=3,
        completions=[],
    )

    assert progress.expected == 3
    assert progress.behind == 3


def test_no_study_day_elapsed_yet_has_no_delay_and_no_projection() -> None:
    """Plano ativado num dia sem estudo: nada esperado ainda, e nada a projetar.

    Sem ritmo medível (`done == 0`) a projeção seria uma divisão por zero disfarçada de
    data — melhor não mostrar data nenhuma que mostrar uma inventada.
    """
    saturday = date(2026, 8, 8)
    sunday = date(2026, 8, 9)
    assert saturday.weekday() == 5
    assert sunday.weekday() == 6

    progress = compute_progress(
        activated_on=saturday,
        today=sunday,
        weekdays=_WEEK,
        total=10,
        completions=[],
    )

    assert progress.expected == 0
    assert progress.done == 0
    assert progress.behind == 0
    assert progress.streak == 0
    assert progress.projected_target is None


def test_no_completion_yet_has_no_projected_target() -> None:
    """Dias decorridos sem nenhuma conclusão: há atraso, mas ritmo nenhum para projetar."""
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=[],
    )

    assert progress.behind == 5
    assert progress.projected_target is None


def test_total_reports_the_planned_lessons() -> None:
    """`total` é o que o dono aprovou no plano — a barra precisa do denominador."""
    progress = compute_progress(
        activated_on=_START,
        today=_FRIDAY,
        weekdays=_WEEK,
        total=10,
        completions=_days(3),
    )

    assert progress.total == 10


def test_plan_without_lessons_is_an_error() -> None:
    """Plano sem lição não tem régua: falha alto em vez de devolver 0/0 e uma data."""
    with pytest.raises(ValueError):
        compute_progress(
            activated_on=_START,
            today=_FRIDAY,
            weekdays=_WEEK,
            total=0,
            completions=[],
        )


@pytest.mark.parametrize("weekdays", [[], ["monday"]])
def test_invalid_cadence_is_an_error(weekdays: list[str]) -> None:
    """Cadência vazia ou com dia inventado é erro — mesma cerca de `planning`."""
    with pytest.raises(ValueError):
        compute_progress(
            activated_on=_START,
            today=_FRIDAY,
            weekdays=weekdays,
            total=10,
            completions=[],
        )
