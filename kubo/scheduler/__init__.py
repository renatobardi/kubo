"""Agendador da fase 1 (ADR-0010): lê schedules.yaml, registra um job cron por
entry e roda cada worker sob contrato com conexão POR execução. BlockingScheduler
síncrono (sem event loop); SIGTERM faz shutdown que espera a run em voo terminar."""

from __future__ import annotations

import signal
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from kubo.distribution.destinations import (
    load_destinations,
    resolve_base_url,
    resolve_destinations,
)
from kubo.embedding import Embedder, GeminiEmbedder
from kubo.errors import ConfigError, format_validation_error
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.runner import run_worker
from kubo.store import client
from kubo.workers.digest import DigestWorker
from kubo.workers.distiller import DistillerWorker
from kubo.workers.feed import FeedWorker

_REPO_ROOT = Path(__file__).parents[2]
_SCHEDULES_PATH = _REPO_ROOT / "schedules.yaml"
_DESTINATIONS_PATH = _REPO_ROOT / "destinations.yaml"
_log = structlog.get_logger()

# Mapa nome→classe HARDCODED (ADR-0010): sem registry/plugin/entry-point dinâmico
# (seria DSL disfarçada). Ativar um worker novo = editar este dict + PR (gate humano).
WORKER_REGISTRY: dict[str, type[Any]] = {
    "feed": FeedWorker,
    "distiller": DistillerWorker,
    "digest": DigestWorker,
}

# Modelo do destilador PINADO POR EVIDÊNCIA (smoke ao vivo 2026-07-11, ADR-0013 §V):
# 10/10 saídas válidas/PT-BR, 0 canary leak. Trocar = editar aqui + PR (gate humano,
# ADR-0010) — nunca fica configurável em schedules.yaml (evitaria o gate).
_DISTILLER_MODEL = "groq/llama-3.3-70b-versatile"
# summary até 8000 chars + entidades no JSON precisa de folga; o default 1024 do
# ApiExecutorConfig truncaria a resposta antes do fim do JSON.
_DISTILLER_MAX_TOKENS = 4096


class ScheduleEntry(BaseModel):
    """Uma entrada agendada: worker + cron + config pública (ADR-0010 item II)."""

    model_config = ConfigDict(extra="forbid")
    worker: str
    cron: str
    config: dict[str, Any] = {}


class Schedules(BaseModel):
    """Config validada de `schedules.yaml`: timezone obrigatória + lista de entries."""

    model_config = ConfigDict(extra="forbid")
    timezone: str
    schedules: list[ScheduleEntry]

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        """Rejeita timezone desconhecida na borda (default do APScheduler é a tz do processo)."""
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"timezone desconhecida: {v}") from exc
        return v


def load_schedules(path: Path = _SCHEDULES_PATH) -> Schedules:
    """Carrega e valida o schedules.yaml; erro vira ConfigError (fronteira)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"schedules {path.name}: YAML não é um mapping")
    try:
        return Schedules.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"schedules inválido: {format_validation_error(exc)}") from exc


def _instantiate(worker_name: str) -> tuple[Any, Embedder | None]:
    """Constrói o worker com suas dependências (marco 8.7, ADR-0013 §VIII).

    O destilador ganha executor (Groq, modelo pinado por evidência) + embedder
    (Gemini, via env); os demais workers não precisam de nenhum dos dois.
    `GeminiEmbedder.from_env()` roda AQUI, no disparo do job — não no boot do
    scheduler (`main`): o scheduler sobe sem `GEMINI_API_KEY`. Se a key faltar
    na hora do disparo, o `ConfigError` sobe a `execute_job` e é registrado como
    `scheduler_job_failed` (o mesmo tratamento de qualquer falha de SETUP que
    ocorre ANTES de `run_worker` abrir a run — não vira `run.error` estruturado,
    porque a run ainda não existe), em vez de derrubar o processo inteiro às
    09:00. Trocar isso por um `run.error` exigiria abrir a run antes das deps
    (factory de embedder avaliada pós-`start_run`) — adiado: a fase 1 tem um
    scheduler único e o log já dá visibilidade.
    """
    if worker_name == "distiller":
        executor = ApiExecutor(
            ApiExecutorConfig(model=_DISTILLER_MODEL, max_tokens=_DISTILLER_MAX_TOKENS)
        )
        return DistillerWorker(executor), GeminiEmbedder.from_env()
    if worker_name == "digest":
        # Destinos + base URL resolvidos do env AQUI (no disparo), como o embedder do
        # distiller: env ausente (chat_id/KUBO_BASE_URL) vira `scheduler_job_failed`,
        # não derruba o processo. O worker recebe tudo resolvido — nunca lê os.environ.
        destinations = resolve_destinations(load_destinations(_DESTINATIONS_PATH))
        return DigestWorker(destinations=destinations, base_url=resolve_base_url()), None
    return WORKER_REGISTRY[worker_name](), None


def execute_job(worker_name: str, config: dict[str, Any]) -> None:
    """Executa um worker agendado com conexão POR execução (ADR-0010): abre a
    conexão, roda sob contrato (run_worker persiste), fecha. Sem handle global de
    DB (um ws de vida longa apodrece num processo que roda dias)."""
    try:
        worker, embedder = _instantiate(worker_name)
        with client.connect(client.config()) as db:
            run_worker(db, worker, config=config, embedder=embedder)
    except Exception:  # noqa: BLE001 — loga estruturado e repropaga; APScheduler não perde o traço
        # Falha de conexão/setup ocorre ANTES de run_worker abrir o run, então não vira
        # run.error estruturado — este log garante visibilidade no formato do resto do módulo.
        _log.exception("scheduler_job_failed", worker=worker_name)
        raise


def build_scheduler(schedules: Schedules) -> BlockingScheduler:
    """Monta o BlockingScheduler com um job cron por entry, tz explícita SEMPRE.

    Valida TUDO eagerly (falha alta antes do start, não horas depois no 1º disparo):
    worker no registry, cron parseável e config coerente com o schema do worker — cada
    falha vira `ConfigError` (padrão de domínio do módulo), nunca exceção crua da lib."""
    tz = ZoneInfo(schedules.timezone)
    scheduler = BlockingScheduler(timezone=tz)
    for entry in schedules.schedules:
        worker_cls = WORKER_REGISTRY.get(entry.worker)
        if worker_cls is None:
            raise ConfigError(f"worker '{entry.worker}' não registrado no WORKER_REGISTRY")
        try:
            trigger = CronTrigger.from_crontab(entry.cron, timezone=tz)
        except ValueError as exc:
            raise ConfigError(
                f"cron inválido para worker '{entry.worker}': {entry.cron!r}"
            ) from exc
        try:
            worker_cls.manifest.config.model_validate(entry.config)
        except ValidationError as exc:
            raise ConfigError(
                f"config inválida para worker '{entry.worker}': {format_validation_error(exc)}"
            ) from exc
        scheduler.add_job(
            execute_job,
            trigger=trigger,
            kwargs={"worker_name": entry.worker, "config": entry.config},
        )
    return scheduler


def make_sigterm_handler(scheduler: BlockingScheduler) -> Callable[[int, Any], None]:
    """Handler de SIGTERM: shutdown(wait=True) — a run em voo termina antes de sair."""

    def _handler(signum: int, frame: Any) -> None:
        _log.info("scheduler_sigterm")
        scheduler.shutdown(wait=True)

    return _handler


def main() -> None:
    """Sobe o agendador: carrega schedules, registra jobs + SIGTERM, bloqueia."""
    schedules = load_schedules()
    scheduler = build_scheduler(schedules)
    signal.signal(signal.SIGTERM, make_sigterm_handler(scheduler))
    _log.info("scheduler_starting", jobs=len(schedules.schedules), timezone=schedules.timezone)
    scheduler.start()
