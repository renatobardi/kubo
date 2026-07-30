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
from datetime import date


@dataclass(frozen=True)
class PlanProgress:
    """Retrato do plano num dia: o que foi feito, o que era esperado e para onde vai."""

    total: int
    done: int
    expected: int
    behind: int
    streak: int
    projected_target: date | None


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
    raise NotImplementedError("KUBO-138: compute_progress")
