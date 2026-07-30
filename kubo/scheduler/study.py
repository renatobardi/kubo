"""Job da véspera (ADR-0043, KUBO-137): gera a lição do PRÓXIMO dia de estudo.

Roda no fim do dia sobre os planos ATIVOS do par (tenant, user) que o scheduler
resolve — mesmo regime de identidade única do distiller/digest. Idempotente por
construção: o índice UNIQUE (plano, dia) e a checagem prévia deixam as janelas de
retry da mesma véspera seguras.

Falha de LLM não é erro do job: a lição daquele plano fica sem gerar, o log registra
e a próxima janela tenta de novo — travar o job condenaria os planos seguintes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from surrealdb import RecordID

from kubo.store import study as study_store
from kubo.store import tenancy
from kubo.study.planning import next_study_day

_log = structlog.get_logger(__name__)


def _display_tz() -> ZoneInfo:
    """Timezone do DONO (env `TZ`) — mesma regra de apresentação da UI.

    O container roda em UTC: derivar o dia de estudo do relógio do processo faria a
    véspera das 21h preparar depois de amanhã.
    """
    return ZoneInfo(os.environ.get("TZ") or "America/Sao_Paulo")


def _next_day(now: datetime, weekdays: list[str]) -> datetime:
    """Meia-noite (na tz do dono) do próximo dia habilitado, em UTC.

    O domínio raciocina em `date`; o storage é UTC. A conversão é aqui, no mesmo
    sentido de `_target_datetime` das rotas — nunca o contrário.
    """
    tz = _display_tz()
    day = next_study_day(after=now.astimezone(tz).date(), weekdays=weekdays)
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def generate_upcoming_lessons(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    now: datetime,
    tutor_factory: Callable[[], Any],
) -> list[RecordID]:
    """Gera a lição do próximo dia de estudo de cada plano ativo; devolve os ids criados.

    `now` entra por PARÂMETRO (nunca `datetime.now()` aqui dentro): o dia-alvo é
    derivado dele, e um relógio implícito tornaria o comportamento do job impossível de
    afirmar em teste.

    `tutor_factory` é a costura injetável (molde de `build_planner` nas rotas): em
    produção resolve persona + executor; em teste devolve um fake, então NENHUM teste
    toca LiteLLM.

    Pula, sem erro: plano cuja lição do dia-alvo já existe (idempotência), plano que
    chegou ao fim das lições (completar plano é fatia 5) e plano cujo tutor falhou.
    """
    plans = study_store.list_active_plans(db, tenant_id=tenant_id, user_id=user_id)
    if not plans:
        _log.info("study.eve.no_active_plans")
        return []
    scope = {"tenant_id": tenant_id, "user_id": user_id}
    user = tenancy.get_user(db, user_id)
    work_context = (user.work_context if user is not None else None) or ""
    tutor = tutor_factory()

    created: list[RecordID] = []
    for plan in plans:
        lesson_id = _lesson_for_plan(
            db,
            plan=plan,
            day=_next_day(now, plan.weekdays),
            scope=scope,
            tutor=tutor,
            work_context=work_context,
        )
        if lesson_id is not None:
            created.append(lesson_id)
    _log.info("study.eve.done", plans=len(plans), created=len(created))
    return created


def _lesson_for_plan(
    db: Any,
    *,
    plan: study_store.StudyPlan,
    day: datetime,
    scope: dict[str, Any],
    tutor: Any,
    work_context: str,
) -> RecordID | None:
    """Gera (ou pula) a lição de UM plano para o dia-alvo; devolve o id criado ou None.

    Extraído do laço para manter uma responsabilidade por função — o laço decide sobre
    QUAIS planos, este decide o que acontece com UM.
    """
    if study_store.get_lesson_for_day(db, plan_id=plan.id, scheduled_for=day, **scope) is not None:
        _log.info("study.eve.already_generated", plan=str(plan.id))
        return None
    entry = study_store.next_unlessoned_entry(db, plan_id=plan.id, **scope)
    if entry is None:
        _log.info("study.eve.plan_exhausted", plan=str(plan.id))
        return None
    output = tutor.generate(
        entry_title=entry.title,
        chapters=study_store.list_chapters_by_ids(db, chapter_ids=entry.chapters, **scope),
        work_context=work_context,
        misses=study_store.recent_misses(db, plan_id=plan.id, **scope),
    )
    if output is None:
        # Sem lição hoje para ESTE plano: a próxima janela do cron tenta de novo. Só
        # ids no log — o conteúdo da lição e o contexto do dono são dado pessoal.
        _log.warning("study.eve.generation_failed", plan=str(plan.id), entry=str(entry.id))
        return None
    lesson = study_store.create_lesson(
        db, plan_id=plan.id, entry_id=entry.id, scheduled_for=day, output=output, **scope
    )
    return lesson.id
