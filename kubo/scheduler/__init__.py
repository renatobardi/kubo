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

from kubo.errors import ConfigError, format_validation_error
from kubo.runtime.runner import run_worker
from kubo.store import client
from kubo.workers.feed import FeedWorker

_SCHEDULES_PATH = Path(__file__).parents[2] / "schedules.yaml"
_log = structlog.get_logger()

# Mapa nome→classe HARDCODED (ADR-0010): sem registry/plugin/entry-point dinâmico
# (seria DSL disfarçada). Ativar um worker novo = editar este dict + PR (gate humano).
WORKER_REGISTRY: dict[str, type] = {"feed": FeedWorker}


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


def execute_job(worker_name: str, config: dict[str, Any]) -> None:
    """Executa um worker agendado com conexão POR execução (ADR-0010): abre a
    conexão, roda sob contrato (run_worker persiste), fecha. Sem handle global de
    DB (um ws de vida longa apodrece num processo que roda dias)."""
    worker_cls = WORKER_REGISTRY[worker_name]
    with client.connect(client.config()) as db:
        run_worker(db, worker_cls(), config=config)


def build_scheduler(schedules: Schedules) -> BlockingScheduler:
    """Monta o BlockingScheduler com um job cron por entry, tz explícita SEMPRE.
    Worker fora do WORKER_REGISTRY falha alto (ConfigError) antes do start."""
    tz = ZoneInfo(schedules.timezone)
    scheduler = BlockingScheduler(timezone=tz)
    for entry in schedules.schedules:
        if entry.worker not in WORKER_REGISTRY:
            raise ConfigError(f"worker '{entry.worker}' não registrado no WORKER_REGISTRY")
        scheduler.add_job(
            execute_job,
            trigger=CronTrigger.from_crontab(entry.cron, timezone=tz),
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
