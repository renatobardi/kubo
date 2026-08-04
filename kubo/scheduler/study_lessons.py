"""Scheduler de Estudos (KUBO-166/168, ADR-0047 §3): transição + geração de lição com IA.

Dois jobs separados:
- `execute_study_transition_job`: busca temas em `scheduled`, checa se hoje é a
  véspera do 1º dia de cadência (ou depois — tolerante a downtime), transiciona
  para `running` + cria a 1ª lição e gera conteúdo com IA (Tutor). Atômico via
  `transition_to_running`.
- `execute_study_lesson_job`: busca temas em `running`, gera a próxima lição
  (registro + conteúdo com IA). Só gera na véspera do próximo dia de cadência.

KUBO-168: a geração com IA chama a persona `tutor` para preencher
`concept`, `scenario`, `application`, `quiz`, `provenance` a partir das
seções do `plan_entry` (KUBO-189: seções, não capítulos). Se o Tutor falha,
a lição fica como placeholder (vazia) — o dono vê a lição agendada, e o
próximo ciclo pode re-tentar.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.personas import resolve_persona
from kubo.store import study as study_store
from kubo.study.planning import next_study_day
from kubo.study.tutor import Tutor

_log = structlog.get_logger(__name__)

# Modelo pinado para o tutor (mesmo princípio do distiller: gate humano no cron).
_TUTOR_MODEL = "anthropic/claude-sonnet-5"
_TUTOR_MAX_TOKENS = 16384
_TUTOR_TIMEOUT = 60.0


def _to_date(dt: datetime) -> date:
    """Converte datetime para date (UTC se tiver tzinfo, senão naive)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).date()
    return dt.date()


def _build_tutor(db: Any, tenant_id: RecordID, user_id: RecordID) -> Tutor:
    """Constrói o Tutor com a persona `tutor` (modelo vem do catálogo)."""
    persona = resolve_persona(db, tenant_id, user_id, "tutor")
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or _TUTOR_MODEL,
            max_tokens=_TUTOR_MAX_TOKENS,
            timeout=_TUTOR_TIMEOUT,
        ),
        max_attempts=1,
    )
    return Tutor(executor=executor, prompt=persona.prompt)


def _generate_lesson_content(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    lesson_id: RecordID,
    entry: study_store.PlanEntry,
    work_context: str,
) -> None:
    """Gera conteúdo de IA para uma lição e preenche o registro (KUBO-168, KUBO-189).

    Busca as seções do plan_entry, chama o Tutor e preenche a lição.
    Se o Tutor falha (None) ou não há seções, a lição fica como placeholder.
    """
    sections = study_store.get_sections_for_entry(
        db, tenant_id=tenant_id, user_id=user_id, entry=entry
    )
    if not sections:
        _log.warning("study.lesson.no_sections", entry=str(entry.id))
        return
    tutor = _build_tutor(db, tenant_id, user_id)
    lesson = tutor.generate(
        entry_title=entry.title,
        sections=sections,
        work_context=work_context,
        misses=(),
    )
    if lesson is None:
        _log.info("study.lesson.tutor_failed", entry=str(entry.id))
        return
    study_store.fill_lesson(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        lesson_id=lesson_id,
        concept=lesson.concept,
        scenario=lesson.scenario,
        application=lesson.application,
        recap=lesson.recap,
        quiz=[item.model_dump() for item in lesson.quiz],
        provenance=[item.model_dump() for item in lesson.provenance],
    )
    _log.info("study.lesson.filled_with_ai", lesson=str(lesson_id), entry=str(entry.id))


def _work_context_for(db: Any, user_id: RecordID) -> str:
    """Busca o work_context do perfil do usuário (string vazia se não houver)."""
    from kubo.store.tenancy import get_user_profile

    profile = get_user_profile(db, user_id=user_id)
    return profile.work_context if profile and profile.work_context else ""


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
    (registro + conteúdo com IA). Atômico via `transition_to_running`.
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
            lesson_id = study_store.transition_to_running(
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
            # KUBO-168: gera conteúdo com IA (se a transição criou a lição).
            if lesson_id is not None:
                work_context = _work_context_for(db, user_id)
                _generate_lesson_content(
                    db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    lesson_id=lesson_id,
                    entry=first_entry,
                    work_context=work_context,
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
    A próxima lição é o próximo `plan_entry` sem lição criada. Gera na véspera
    do próximo dia de cadência OU no próprio dia (downtime-tolerant — se o
    scheduler não rodou na véspera, gera no dia). O índice UNIQUE
    `lesson_plan_day` impede duplicata (double-fire na 1ª véspera é silencioso).

    KUBO-168: após criar o registro, chama o Tutor para preencher com IA
    (concept, scenario, application, quiz, provenance). Se o Tutor falha,
    a lição fica como placeholder (vazia).
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
            # after = today - 1 para incluir today como candidato (downtime recovery).
            next_day = next_study_day(after=today - timedelta(days=1), weekdays=plan.weekdays)
            eve = next_day - timedelta(days=1)
            # Janela [eve, next_day]: véspera (normal) ou dia (downtime recovery).
            if today < eve or today > next_day:
                continue
            lesson_date = datetime(next_day.year, next_day.month, next_day.day)
            # Re-tenta lição placeholder (Tutor falhou antes) antes de criar nova.
            lesson_id = study_store.get_pending_lesson_for_entry(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                plan_id=plan.id,
                plan_entry_id=next_entry.id,
            )
            if lesson_id is None:
                lesson_id = study_store.create_lesson(
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
            else:
                _log.info(
                    "study.lesson.retry",
                    topic=str(topic.id),
                    entry=str(next_entry.id),
                    lesson=str(lesson_id),
                )
            # KUBO-168: gera conteúdo com IA.
            work_context = _work_context_for(db, user_id)
            _generate_lesson_content(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                lesson_id=lesson_id,
                entry=next_entry,
                work_context=work_context,
            )
        except StoreError:
            # StoreError = duplicata (UNIQUE lesson_plan_day) ou falha de DB.
            # Duplicata é esperada (double-fire na 1ª véspera); falha de DB
            # será recuperada no próximo ciclo. Log warning, não exception.
            _log.warning("study.lesson.skipped", topic=str(topic.id))
        except Exception:  # noqa: BLE001 — isola o tema: loga e segue
            _log.exception("study.lesson.unexpected", topic=str(topic.id))
