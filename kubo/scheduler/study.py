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
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from surrealdb import RecordID

from kubo.distribution.config import resolve_base_url
from kubo.store import study as study_store
from kubo.store import tenancy
from kubo.study.planning import next_study_day, normalized_weekdays
from kubo.study.progress import PlanProgress, compute_progress

_log = structlog.get_logger(__name__)

# Atraso a partir do qual a cutucada SUBSTITUI o sino (ADR-0043, KUBO-138). Uma lição de
# atraso é vida normal; duas é o ponto em que avisar ajuda em vez de ser ruído.
_NUDGE_THRESHOLD = 2


def _display_tz() -> ZoneInfo:
    """Timezone do DONO (env `TZ`) — mesma regra de apresentação da UI.

    O container roda em UTC: derivar o dia de estudo do relógio do processo faria a
    véspera das 21h preparar depois de amanhã.
    """
    return ZoneInfo(os.environ.get("TZ") or "America/Sao_Paulo")


def _midnight_utc(day: date, tz: ZoneInfo) -> datetime:
    """Meia-noite do dia na tz do dono, em UTC — a chave de `scheduled_for` no banco.

    O domínio raciocina em `date`; o storage é UTC. A conversão é aqui, no mesmo
    sentido de `_target_datetime` das rotas — nunca o contrário.
    """
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def _next_day(now: datetime, weekdays: list[str]) -> datetime:
    """Meia-noite (na tz do dono) do próximo dia habilitado, em UTC."""
    tz = _display_tz()
    return _midnight_utc(next_study_day(after=now.astimezone(tz).date(), weekdays=weekdays), tz)


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
        try:
            lesson_id = _lesson_for_plan(
                db,
                plan=plan,
                day=_next_day(now, plan.weekdays),
                scope=scope,
                tutor=tutor,
                work_context=work_context,
            )
        except ValueError:
            # Cadência corrompida no banco (dia inválido) derruba ESTE plano, não a noite
            # inteira: os planos seguintes ainda têm lição amanhã.
            _log.warning("study.eve.invalid_cadence", plan=str(plan.id))
            continue
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
        db,
        plan_id=plan.id,
        entry_id=entry.id,
        scheduled_for=day,
        output=output,
        **scope,
    )
    return lesson.id


# --- Sino do dia (KUBO-138) ------------------------------------------------------------
#
# Roda de MANHÃ (o oposto da véspera acima) sobre os planos ativos do mesmo par
# (tenant, user). Uma mensagem por plano por dia, no máximo: o cron de 1x/dia É a cerca
# anti-spam — nenhuma tabela de "notificação enviada".


def send_daily_study_bell(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    now: datetime,
    notifier: Callable[[str], None],
) -> list[str]:
    """Avisa o dono sobre o estudo do dia; devolve as mensagens enviadas.

    Por plano ativo: sem dia de estudo hoje ou sem lição gerada, silêncio (nada é
    enviado "por via das dúvidas"). Plano cujas lições acabaram vira `completed` com
    mensagem de parabéns. Atraso de 2 lições ou mais troca o sino pela cutucada com a
    data-alvo projetada — é UMA mensagem ou OUTRA, nunca as duas.

    `now` entra por PARÂMETRO e `notifier` é a costura injetável (molde de
    `tutor_factory`): em produção manda pelo caminho outbound do Telegram, em teste é
    um fake — NENHUM teste toca a rede.

    Notificador que falha não derruba o job: o log registra e os planos seguintes
    seguem (mesma postura do digest).
    """
    tz = _display_tz()
    today = now.astimezone(tz).date()
    scope: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id}
    # Plano PAUSADO já não está aqui: pausar é o pedido de parar de ser cobrado, e uma
    # varredura própria e mais larga reabriria a cobrança pelas costas da pausa.
    plans = study_store.list_active_plans(db, **scope)
    sent: list[str] = []
    for plan in plans:
        try:
            message = _bell_for_plan(db, plan=plan, today=today, tz=tz, scope=scope)
        except ValueError:
            # Cadência corrompida no banco (dia inválido) derruba ESTE plano, não o job:
            # sem a cerca, um registro estragado calaria o sino de todos os planos.
            _log.warning("study.bell.invalid_cadence", plan=str(plan.id))
            continue
        if message is None:
            continue
        try:
            notifier(message)
        except Exception:  # noqa: BLE001 — indisponibilidade de minutos não pode matar o cron
            # Só o id do plano: o texto carrega título de material e é dado pessoal.
            _log.warning("study.bell.notify_failed", plan=str(plan.id))
            continue
        sent.append(message)
    _log.info("study.bell.done", plans=len(plans), sent=len(sent))
    return sent


def _bell_for_plan(
    db: Any,
    *,
    plan: study_store.StudyPlan,
    today: date,
    tz: ZoneInfo,
    scope: dict[str, Any],
) -> str | None:
    """A mensagem do dia para UM plano, ou None quando o dia é de silêncio.

    A ordem é comportamento: fora da cadência nada acontece; a completude vem ANTES da
    lição de hoje (no dia seguinte à última lição a véspera não gerou nada, e um plano
    atrás do guard de "existe lição" nunca fecharia); e a cutucada SUBSTITUI o sino, em
    vez de somar-se a ele.
    """
    if today.weekday() not in normalized_weekdays(plan.weekdays):
        return None
    entries = study_store.list_plan_entries(db, plan_id=plan.id, **scope)
    title = _topic_title(db, plan=plan, scope=scope)
    done = study_store.count_completed_lessons(db, plan_id=plan.id, **scope)
    if entries and done >= len(entries):
        study_store.complete_plan(db, plan_id=plan.id, **scope)
        _log.info("study.bell.plan_completed", plan=str(plan.id))
        return f"Plano concluído: {title} — as {len(entries)} lições foram estudadas. Parabéns!"
    lesson = study_store.get_lesson_for_day(
        db, plan_id=plan.id, scheduled_for=_midnight_utc(today, tz), **scope
    )
    if lesson is None:
        _log.info("study.bell.no_lesson", plan=str(plan.id))
        return None
    link = f"{resolve_base_url()}/study/lessons/{lesson.id.id}"
    progress = _progress_of(db, plan=plan, entries=entries, today=today, tz=tz, scope=scope)
    if progress.behind >= _NUDGE_THRESHOLD:
        return _nudge_text(title=title, progress=progress, link=link)
    return f"Lição de hoje: {title}{_lesson_position(lesson, entries)}\n{link}"


def _topic_title(db: Any, *, plan: study_store.StudyPlan, scope: dict[str, Any]) -> str:
    """Título do tema do plano — o que o dono reconhece na mensagem."""
    topic = study_store.get_topic(db, topic_id=plan.topic, **scope)
    return topic.title if topic is not None else "seu plano de estudo"


def _lesson_position(lesson: study_store.Lesson, entries: list[study_store.PlanEntry]) -> str:
    """Sufixo `— lição N de M` da mensagem; vazio quando a lição não casa com o plano.

    Diz ao dono ONDE ele está no plano sem uma leitura nova: a posição vem das lições
    já carregadas para a régua.
    """
    seq = next((e.seq for e in entries if str(e.id) == str(lesson.plan_entry)), None)
    return f" — lição {seq} de {len(entries)}" if seq is not None else ""


def _progress_of(
    db: Any,
    *,
    plan: study_store.StudyPlan,
    entries: list[study_store.PlanEntry],
    today: date,
    tz: ZoneInfo,
    scope: dict[str, Any],
) -> PlanProgress:
    """A régua do plano hoje — derivada (ADR-0043), nunca lida de uma tabela de meta.

    As conclusões saem do banco em UTC e viram o dia LOCAL do dono aqui: comparar dias
    no relógio do container faria a lição das 21h contar para o dia seguinte.
    """
    completions = study_store.completion_dates(db, plan_id=plan.id, **scope)
    return compute_progress(
        activated_on=(plan.activated_at or plan.created_at).astimezone(tz).date(),
        today=today,
        weekdays=plan.weekdays,
        total=len(entries),
        completions=[moment.astimezone(tz).date() for moment in completions],
        paused_days=plan.paused_days,
    )


def _nudge_text(*, title: str, progress: PlanProgress, link: str) -> str:
    """A cutucada: quantas lições faltam e para onde o ritmo REAL está empurrando o fim.

    Sem ritmo medível não há data a mostrar (o dono nunca concluiu nada) — a cutucada
    sai assim mesmo, só sem a projeção: inventar uma data seria pior que omiti-la.
    """
    target = progress.projected_target
    pace = f" No ritmo atual, você termina em {target:%d/%m/%Y}." if target is not None else ""
    return f"{title}: {progress.behind} lições atrasadas.{pace}\n{link}"
