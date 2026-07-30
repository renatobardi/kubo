"""Progresso do plano de estudo (ADR-0043, KUBO-138): a régua derivada.

Domínio PURO — `date` sem timezone, sem banco, sem rede. A meta NUNCA é entidade
(ADR-0043): progresso, atraso, streak e data-alvo projetada são DERIVADOS do plano
(ativação + cadência), das lições planejadas e dos registros de estudo. Nenhuma
tabela nova guarda "meta batida".

Pausa CONGELA a régua: dias pausados não contam como esperados, senão retomar um
plano depois de duas semanas paradas chegaria com dez lições de atraso fabricadas.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from kubo.study.planning import compute_target_date, next_study_day, normalized_weekdays

_DAYS_IN_WEEK = 7


@dataclass(frozen=True)
class PlanProgress:
    """Retrato do plano num dia: o que foi feito, o que era esperado e para onde vai."""

    total: int
    done: int
    expected: int
    behind: int
    streak: int
    projected_target: date | None


def count_study_days(*, start: date, end: date, weekdays: Collection[str]) -> int:
    """Quantos dias habilitados existem em [`start`, `end`] — extremos INCLUSIVE.

    É a unidade da régua: o esperado de um plano são os dias de estudo decorridos, e
    os dias congelados por uma pausa são contados com a mesma função (o retomar
    pergunta quantos dias de estudo passaram enquanto o plano esteve parado). `end`
    antes de `start` é zero, não erro — pausar e retomar no mesmo dia não congela nada.
    """
    enabled = normalized_weekdays(weekdays)
    if end < start:
        return 0
    # Semanas inteiras rendem a cadência cheia; só o resto (< 7 dias) é varrido.
    weeks, rest = divmod((end - start).days + 1, _DAYS_IN_WEEK)
    tail = sum(1 for offset in range(rest) if (start + timedelta(days=offset)).weekday() in enabled)
    return weeks * len(enabled) + tail


def compute_progress(
    *,
    activated_on: date,
    today: date,
    weekdays: Collection[str],
    total: int,
    completions: Sequence[date],
    paused_days: int = 0,
) -> PlanProgress:
    """Progresso do plano em `today`, derivado da cadência e dos estudos concluídos.

    `completions` são as datas LOCAIS dos registros de estudo (uma por registro, não
    por dia: duas lições no mesmo dia contam duas). `paused_days` é o acumulador de
    dias de estudo congelados pelas pausas.
    """
    if total < 1:
        raise ValueError("um plano precisa de ao menos uma lição")
    elapsed = count_study_days(start=activated_on, end=today, weekdays=weekdays)
    # A régua para no fim do plano (`min`) e nunca anda para trás (`max`): sem os dois
    # limites, um acumulador de pausa grande ou um plano velho viraria atraso fabricado.
    expected = min(total, max(0, elapsed - max(0, paused_days)))
    done = len(completions)
    return PlanProgress(
        total=total,
        done=done,
        expected=expected,
        behind=max(0, expected - done),
        streak=_streak(
            today=today,
            activated_on=activated_on,
            weekdays=weekdays,
            completions=completions,
        ),
        projected_target=_projected_target(
            today=today, weekdays=weekdays, total=total, done=done, expected=expected
        ),
    )


def _streak(
    *,
    today: date,
    activated_on: date,
    weekdays: Collection[str],
    completions: Sequence[date],
) -> int:
    """Dias de ESTUDO consecutivos concluídos, contados de `today` para trás.

    Dia fora da cadência é pulado sem quebrar nada: o fim de semana de quem estuda
    seg-sex não é falta. HOJE é o único dia habilitado que pode estar pendente sem
    zerar a contagem — o dia corrente ainda não acabou, e sem esta folga o painel
    mostraria "0 dias" toda manhã.
    """
    enabled = normalized_weekdays(weekdays)
    studied = set(completions)
    days = 0
    day = today
    while day >= activated_on:
        if day.weekday() in enabled:
            if day in studied:
                days += 1
            elif day != today:
                break
        day -= timedelta(days=1)
    return days


def _projected_target(
    *, today: date, weekdays: Collection[str], total: int, done: int, expected: int
) -> date | None:
    """Data do fim do plano no ritmo REAL (`done`/`expected`), ou None sem o que projetar.

    Sem conclusão nenhuma não há ritmo medível, e sem dia de estudo decorrido a conta
    seria uma divisão por zero disfarçada de data. Plano concluído também devolve None:
    não há lição restante para prever, e a tela mostra o estado concluído.

    O ritmo é capado em uma lição por dia de estudo: quem estudou duas num dia está
    adiantado, mas o plano ainda gasta um dia habilitado por lição restante.
    """
    remaining = total - done
    if remaining < 1 or done < 1 or expected < 1:
        return None
    # Aritmética inteira (e não `remaining / (done / expected)`): a divisão em ponto
    # flutuante erra por um dia em ritmos como 2/5, e o alvo mostrado seria o do dia errado.
    days_ahead = remaining if done >= expected else -(-remaining * expected // done)
    first = next_study_day(after=today, weekdays=weekdays)
    return compute_target_date(start=first, weekdays=weekdays, lesson_count=days_ahead)
