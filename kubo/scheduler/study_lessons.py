"""Scheduler de Estudos (KUBO-166, ADR-0047 §3): transição scheduled→running + geração de lição.

Dois jobs separados:
- `execute_study_transition_job`: busca temas em `scheduled`, checa se hoje é a
  véspera do 1º dia de cadência (ou depois — tolerante a downtime), transiciona
  para `running` + cria a 1ª lição (registro vazio — KUBO-168 traz a geração
  com IA). Atômico via `transition_to_running`.
- `execute_study_lesson_job`: busca temas em `running`, gera a próxima lição
  (registro vazio — KUBO-168 traz a geração com IA). Só gera na véspera do
  próximo dia de cadência (modelo de véspera, como KUBO-137).

Não gera lições para `scheduled` (regular) ou `archived` — o job de lição filtra
só `running`. A 1ª lição é parte da transição, não da geração regular.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import study as study_store
from kubo.study.planning import next_study_day

_log = structlog.get_logger(__name__)


def _to_date(dt: datetime, tz: timezone | None = None) -> date:
    """Converte datetime para date na timezone dada (ou UTC se não tiver tzinfo)."""
    if dt.tzinfo is not None:
        if tz is not None:
            return dt.astimezone(tz).date()
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
    `activated_at` + `weekdays`. Se `today` é a véspora desse dia (ou depois —
    tolerante a downtime), transiciona para `running` e cria a 1ª lição
    (registro vazio). Atômico via `transition_to_running`.
    """
    topics = study_store.list_topics_by_state(
        db, tenant_id=tenant_id, user_id=user_id, state="scheduled"
    )
    for topic in topics:
        try:
            plan, entries = study_store.get_plan_for_topic(
                db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id
            )
            if plan is None or plan.activated_at is None or not entries:
                continue
            if not plan.weekdays:
                _log.warning("study.transition.no_cadence", topic=str(topic.id))
                continue
            activated_on = _to_date(plan.activated_at)
            first_day = next_study_day(after=activated_on, weekdays=plan.weekdays)
            eve = first_day - timedelta(days=1)
            if today < eve:
                continue  # ainda não chegou a véspera
            # Véspera (ou depois — tolerante a downtime): transiciona + cria 1ª lição.
            first_entry = entries[0]
            lesson_date = datetime(first_day.year, first_day.month, first_day.day)
            study_store.transition_to_running(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                topic_id=topic.id,
                plan_id=plan.id,
                plan_entry_id=first_entry.id,
                scheduled_for=lesson_date,
            )
            _log.info(
                "study.transition.scheduled_to_running",
                topic=str(topic.id),
                first_lesson=first_day.isoformat(),
            )
        except StoreError:
            _log.exception("study.transition.failed", topic=str(topic.id))
        except Exception:  # noqa: BLE001 — isola o tema: loga e segue
            _log.exception("study.transition.unexpected", topic=str(topic.id))


def execute_study_lesson_job(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    today: date,
) -> None:
    """Job de lição: gera a próxima lição para temas em `running`.

    Filtra SÓ `running` — não gera para `scheduled` (regular) ou `archived`.
    A próxima lição é o próximo `plan_entry` sem lição criada. Só gera na
    véspera do próximo dia de cadência (modelo de véspera, como KUBO-137).
    KUBO-168 traz a geração com IA (concept, scenario, application, quiz).
    """
    topics = study_store.list_topics_by_state(
        db, tenant_id=tenant_id, user_id=user_id, state="running"
    )
    for topic in topics:
        try:
            plan, entries = study_store.get_plan_for_topic(
                db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id
            )
            if plan is None or not entries or not plan.weekdays:
                continue
            done = study_store.count_lessons_for_plan(
                db, tenant_id=tenant_id, user_id=user_id, plan_id=plan.id
            )
            if done >= len(entries):
                continue  # plano concluído
            next_entry = entries[done]
            next_day = next_study_day(after=today, weekdays=plan.weekdays)
            eve = next_day - timedelta(days=1)
            if today != eve:
                continue  # só gera na véspera do próximo dia de cadência
            lesson_date = datetime(next_day.year, next_day.month, next_day.day)
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
                scheduled_for=next_day.isoformat(),
            )
        except StoreError:
            _log.exception("study.lesson.failed", topic=str(topic.id))
        except Exception:  # noqa: BLE001 — isola o tema: loga e segue
            _log.exception("study.lesson.unexpected", topic=str(topic.id))
