"""Scheduler de Estudos (KUBO-166, ADR-0047 §3): transição scheduled→running + geração de lição.

Dois jobs separados:
- `execute_study_transition_job`: busca temas em `scheduled`, checa se hoje é a
  véspera do 1º dia de cadência, transiciona para `running` + cria a 1ª lição
  (registro vazio — KUBO-168 traz a geração com IA).
- `execute_study_lesson_job`: busca temas em `running`, gera a próxima lição
  (registro vazio — KUBO-168 traz a geração com IA).

Não gera lições para `scheduled` (regular) ou `archived` — o job de lição filtra
só `running`. A 1ª lição é parte da transição, não da geração regular.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.store import study as study_store
from kubo.study.planning import next_study_day

_log = structlog.get_logger(__name__)


def _to_date(dt: datetime) -> date:
    """Converte datetime para date (UTC se não tiver tzinfo)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).date()
    return dt.date()


def execute_study_transition_job(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    today: date,
) -> None:
    """Job de transição: scheduled → running na véspera do 1º dia de cadência.

    Para cada tema em `scheduled`, calcula o 1º dia de cadência a partir de
    `activated_at` + `weekdays`. Se `today` é a véspera desse dia, transiciona
    para `running` e cria a 1ª lição (registro vazio).
    """
    topics = study_store.list_topics_by_state(
        db, tenant_id=tenant_id, user_id=user_id, state="scheduled"
    )
    for topic in topics:
        plan, entries = study_store.get_plan_for_topic(
            db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id
        )
        if plan is None or plan.activated_at is None or not entries:
            continue
        activated_on = _to_date(plan.activated_at)
        first_day = next_study_day(after=activated_on, weekdays=plan.weekdays)
        eve = first_day - timedelta(days=1)
        if today != eve:
            continue
        # Véspera do 1º dia: transiciona + cria 1ª lição.
        first_entry = entries[0]
        lesson_date = datetime(first_day.year, first_day.month, first_day.day)
        study_store.create_lesson(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            plan_id=plan.id,
            plan_entry_id=first_entry.id,
            scheduled_for=lesson_date,
        )
        study_store.set_topic_state(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            topic_id=topic.id,
            state="running",
        )
        _log.info(
            "study.transition.scheduled_to_running",
            topic=str(topic.id),
            first_lesson=first_day.isoformat(),
        )


def execute_study_lesson_job(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    today: date,
) -> None:
    """Job de lição: gera a próxima lição para temas em `running`.

    Filtra SÓ `running` — não gera para `scheduled` (regular) ou `archived`.
    A próxima lição é o próximo `plan_entry` sem lição criada. KUBO-168 traz
    a geração com IA (concept, scenario, application, quiz).
    """
    topics = study_store.list_topics_by_state(
        db, tenant_id=tenant_id, user_id=user_id, state="running"
    )
    for topic in topics:
        plan, entries = study_store.get_plan_for_topic(
            db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id
        )
        if plan is None or not entries:
            continue
        done = study_store.count_lessons_for_plan(
            db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id
        )
        if done >= len(entries):
            continue  # plano concluído
        next_entry = entries[done]
        lesson_day = next_study_day(after=today - timedelta(days=1), weekdays=plan.weekdays)
        lesson_date = datetime(lesson_day.year, lesson_day.month, lesson_day.day)
        try:
            study_store.create_lesson(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                plan_id=plan.id,
                plan_entry_id=next_entry.id,
                scheduled_for=lesson_date,
            )
            _log.info(
                "study.lesson.generated",
                topic=str(topic.id),
                entry=str(next_entry.id),
                scheduled_for=lesson_day.isoformat(),
            )
        except Exception:  # noqa: BLE001 — lição já existe para o dia (UNIQUE)
            _log.warning("study.lesson.duplicate_skip", topic=str(topic.id))
