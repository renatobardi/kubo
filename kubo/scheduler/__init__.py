"""Agendador da fase 1 (ADR-0010): lê schedules.yaml, registra um job cron por
entry e roda cada worker sob contrato com conexão POR execução. BlockingScheduler
síncrono (sem event loop); SIGTERM faz shutdown que espera a run em voo terminar.

A partir do KUBO-44 (ADR-0028): o job `digest` passa a ser montado a partir do
singleton `settings` (`digest_cron` + timezone do schedules.yaml), e um poll de 5
minutos reage a mudanças de horário sem restartar o processo.
"""

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
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from kubo.distribution.destinations import resolve_base_url
from kubo.embedding import Embedder, GeminiEmbedder
from kubo.errors import ConfigError, format_validation_error
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.runner import run_worker
from kubo.scheduler.sweep import DEST_DISPATCH, SWEEP_DISPATCH
from kubo.store import client
from kubo.store import destinations as destination_store
from kubo.store import settings as settings_store
from kubo.store.knowledge import active_sources
from kubo.workers.distiller import DistillerWorker
from kubo.workers.registry import WORKER_REGISTRY

_REPO_ROOT = Path(__file__).parents[2]
_SCHEDULES_PATH = _REPO_ROOT / "schedules.yaml"
_log = structlog.get_logger()

# Intervalo de polling da config do digest — 5 minutos (ADR-0028 §4).
_DIGEST_POLL_MINUTES = 5

# Teto de destilados por digest — constante pinada no scheduler (ADR-0028 §2), espelho
# de `_DISTILLER_MODEL`. O worker ainda carrega o default 50, mas o scheduler passa
# o valor explicitamente para não depender de default oculto.
_DIGEST_MAX_ITEMS = 50

# Modelo do destilador PINADO POR EVIDÊNCIA (smoke ao vivo 2026-07-11, ADR-0013 §V):
# 10/10 saídas válidas/PT-BR, 0 canary leak. Trocar = editar aqui + PR (gate humano,
# ADR-0010) — nunca fica configurável em schedules.yaml (evitaria o gate).
_DISTILLER_MODEL = "groq/llama-3.3-70b-versatile"
# summary até 8000 chars + entidades no JSON precisa de folga; o default 1024 do
# ApiExecutorConfig truncaria a resposta antes do fim do JSON.
_DISTILLER_MAX_TOKENS = 4096


class WorkerEntry(BaseModel):
    """Uma entrada agendada de WORKER: worker + cron + config pública (ADR-0010 item II)."""

    model_config = ConfigDict(extra="forbid")
    worker: str
    cron: str
    config: dict[str, Any] = {}


class SweepEntry(BaseModel):
    """Uma entrada de SWEEP de coleta (#108, ADR-0025 §4): varre os Cadastros ativos de um
    `kind` e dispara um run por Cadastro. `sweep` é o KIND a varrer (dado — `rss`/`github-repo`),
    não um nome de worker (o mapa kind→worker é fixo em código, `SWEEP_DISPATCH`). Sem `config`: a
    config de cada run vem do Cadastro, não da entry. Campo obrigatório disjunto (`sweep`)
    desambigua a união sem discriminador, como `worker`."""

    model_config = ConfigDict(extra="forbid")
    sweep: str
    cron: str


class Schedules(BaseModel):
    """Config validada de `schedules.yaml`: timezone obrigatória + lista de entries — cada
    entry é `WorkerEntry` OU `SweepEntry` (união em modo smart do Pydantic v2: os campos
    obrigatórios disjuntos `worker` vs `sweep` bastam para desambiguar sem `discriminator=`
    explícito, ADR-0010/ADR-0025).

    Agendamento de FLOW (`FlowEntry`, ADR-0022) foi APOSENTADO no #110: o único flow `scheduled`
    era o `pipeline`, cuja coleta migrou pro sweep `github-repo`. A capacidade foi removida, não
    proibida — reintroduzir exige só reanimar `FlowEntry`+`_add_flow_job` do histórico (nota de
    supersede no ADR-0022)."""

    model_config = ConfigDict(extra="forbid")
    timezone: str
    schedules: list[WorkerEntry | SweepEntry]

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
    ocorre ANTES de `run_worker` abrir o run — não vira `run.error` estruturado,
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
    return WORKER_REGISTRY[worker_name](), None


def execute_job(worker_name: str, config: dict[str, Any]) -> None:
    """Executa um worker agendado com conexão POR execução (ADR-0010): abre a
    conexão, roda sob contrato (run_worker persiste), fecha. Sem handle global de
    DB (um ws de vida longa apodrece num processo que roda dias).
    """
    try:
        worker, embedder = _instantiate(worker_name)
        with client.connect(client.config()) as db:
            run_worker(db, worker, config=config, embedder=embedder)
    except Exception:  # noqa: BLE001 — loga estruturado e repropaga; APScheduler não perde o traço
        # Falha de conexão/setup ocorre ANTES de run_worker abrir o run, então não vira
        # run.error estruturado — este log garante visibilidade no formato do resto do módulo.
        _log.exception("scheduler_job_failed", worker=worker_name)
        raise


def execute_sweep_job(kind: str) -> None:
    """Executa o sweep de coleta de um `kind` (#108, ADR-0025 §4): lê os Cadastros ATIVOS e
    dispara UM run por Cadastro (preserva 'um run = um feed', ADR-0009). Loop query→run_worker,
    ISOLADO por Cadastro — a falha de um NÃO condena os demais (`try`/`continue`), e cada run
    abre a PRÓPRIA conexão para que um ws morto num Cadastro não derrube o reste do sweep
    (a listagem dos ativos usa uma conexão curta antes). Sem run 'pai', sem retry, sem estado
    de orquestração: os registros do sweep SÃO as runs que `run_worker` persiste — cruzar
    essa linha entraria no escopo negativo (invariante 7). `kind` já foi validado contra
    `SWEEP_DISPATCH` em `_add_sweep_job` (falha eager no build); o `.get` aqui é defensivo."""
    dispatch = SWEEP_DISPATCH.get(kind)
    if dispatch is None:  # inalcançável via build_scheduler (validado eager); guarda de fiação
        raise ConfigError(f"sweep de kind '{kind}' sem despacho em SWEEP_DISPATCH")
    with client.connect(client.config()) as db:
        sources = active_sources(db, kind=kind)
    dispatched = 0
    failed = 0
    for source in sources:
        try:
            with client.connect(client.config()) as db:
                run_worker(db, dispatch.worker_factory(), config=dispatch.build_config(source))
            dispatched += 1
        except Exception:  # noqa: BLE001 — isola o Cadastro: loga e segue (run_worker já estrutura erro de worker)
            failed += 1
            # Só id + kind: a canonical é entrada do dono e pode trazer token na query string
            # (feed privado) — logá-la vazaria segredo (CLAUDE.md §Logs). O id resolve a fonte.
            _log.exception("sweep_run_failed", kind=kind, source=str(source.id))
    _log.info("sweep_done", kind=kind, total=len(sources), dispatched=dispatched, failed=failed)


def execute_digest_sweep_job() -> None:
    """Executa o sweep de destinos do digest (ADR-0029): lê destinos ATIVOS do
    banco, checa `distribution_paused` uma vez antes do loop (zero runs se
    pausado) e dispara UM run por destino. Uma falha em um destino não impede
    os demais; cada run abre sua própria conexão.
    """
    try:
        with client.connect(client.config()) as settings_db:
            try:
                settings = settings_store.get_settings(settings_db)
            except Exception:  # noqa: BLE001 — defensivo: estado operacional desconhecido
                _log.exception("digest_settings_read_failed")
                paused = True
            else:
                paused = settings.distribution_paused if settings else False
        _log.info("digest_pause_read", distribution_paused=paused)
        if paused:
            _log.info("digest_sweep_skipped", reason="paused")
            return

        base_url = resolve_base_url()

        with client.connect(client.config()) as list_db:
            destination_list = destination_store.active_destinations(list_db)

        dispatched = 0
        failed = 0
        for destination in destination_list:
            factory = DEST_DISPATCH.get(destination.channel)
            if factory is None:
                _log.warning(
                    "digest_sweep_channel_ignored",
                    destination=str(destination.id),
                    channel=destination.channel,
                )
                failed += 1
                continue
            try:
                worker = factory(destination, base_url)
                with client.connect(client.config()) as run_db:
                    run_worker(
                        run_db,
                        worker,
                        config={"max_items": _DIGEST_MAX_ITEMS},
                        embedder=None,
                    )
                dispatched += 1
            except Exception:  # noqa: BLE001 — isola o destino: loga e segue
                failed += 1
                _log.exception(
                    "digest_sweep_run_failed",
                    destination=str(destination.id),
                    channel=destination.channel,
                )
        _log.info(
            "digest_sweep_done",
            total=len(destination_list),
            dispatched=dispatched,
            failed=failed,
        )
    except Exception:  # noqa: BLE001 — loga e repropaga falhas de setup
        _log.exception("digest_sweep_failed")
        raise


def _cron_trigger(cron: str, tz: ZoneInfo, *, label: str) -> CronTrigger:
    """Parseia `cron` em `CronTrigger`, com `ConfigError` legível (padrão de domínio do
    módulo) no lugar da `ValueError` crua da lib — `label` identifica a entry no erro."""
    try:
        return CronTrigger.from_crontab(cron, timezone=tz)
    except ValueError as exc:
        raise ConfigError(f"cron inválido para {label}: {cron!r}") from exc


def _add_worker_job(scheduler: BlockingScheduler, entry: WorkerEntry, tz: ZoneInfo) -> None:
    """Valida e registra o job de uma `WorkerEntry`: worker no registry, cron parseável,
    config coerente com o schema do worker — cada falha vira `ConfigError` eagerly, antes
    do scheduler subir."""
    worker_cls = WORKER_REGISTRY.get(entry.worker)
    if worker_cls is None:
        raise ConfigError(f"worker '{entry.worker}' não registrado no WORKER_REGISTRY")
    trigger = _cron_trigger(entry.cron, tz, label=f"worker '{entry.worker}'")
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


def _add_sweep_job(scheduler: BlockingScheduler, entry: SweepEntry, tz: ZoneInfo) -> None:
    """Valida e registra o job de uma `SweepEntry` (#108): o `kind` tem que estar em
    `SWEEP_DISPATCH` e o cron ser parseável — cada falha vira `ConfigError` EAGER (antes do
    start). Validar o kind no build torna 'kind desconhecido em runtime' impossível por
    construção (o `.get` de `execute_sweep_job` nunca vê um kind não mapeado)."""
    if entry.sweep not in SWEEP_DISPATCH:
        raise ConfigError(
            f"sweep de kind '{entry.sweep}' sem despacho em SWEEP_DISPATCH "
            f"(despacháveis: {sorted(SWEEP_DISPATCH)})"
        )
    trigger = _cron_trigger(entry.cron, tz, label=f"sweep '{entry.sweep}'")
    scheduler.add_job(execute_sweep_job, trigger=trigger, kwargs={"kind": entry.sweep})


def _digest_trigger(cron: str, tz: ZoneInfo) -> CronTrigger:
    """Parseia o `digest_cron` do settings com o label fixo `digest`."""
    return _cron_trigger(cron, tz, label="digest")


def _add_digest_job(
    scheduler: BlockingScheduler, settings: settings_store.Settings, tz: ZoneInfo
) -> None:
    """Registra o job `digest` a partir do `digest_cron` do singleton settings (KUBO-44).

    O horário não vem mais do `schedules.yaml` — a timezone continua vindo de lá. O job
    agora dispara o sweep de destinos (`execute_digest_sweep_job`), não mais o worker
    monolítico `digest` (ADR-0029).
    """
    trigger = _digest_trigger(settings.digest_cron, tz)
    scheduler.add_job(
        execute_digest_sweep_job,
        trigger=trigger,
        id="digest",
    )


def _check_and_reschedule_digest(
    db: Any,
    scheduler: BlockingScheduler,
    tz: ZoneInfo,
    last_cron: str,
) -> str:
    """Lê `settings` e reagenda o job `digest` se `digest_cron` mudou.

    Devolve o novo `last_cron` (igual ou atualizado). Erros de DB ou cron inválido
    são logados e NÃO levantam — o agendador mantém o comportamento atual
    (ADR-0028 §4). Mudanças vazias/mesmo cron não geram log, só o efetivo reschedule.
    """
    try:
        settings = settings_store.get_settings(db)
    except Exception:  # noqa: BLE001 — erro de leitura não derruba o poll
        _log.exception("digest_poll_failed", phase="read_settings")
        return last_cron
    if settings is None:
        _log.warning("digest_poll_no_settings")
        return last_cron

    if settings.digest_cron == last_cron:
        return last_cron

    try:
        new_trigger = _digest_trigger(settings.digest_cron, tz)
    except ConfigError:
        _log.error("digest_poll_invalid_cron", cron=settings.digest_cron)
        return last_cron

    try:
        scheduler.reschedule_job("digest", trigger=new_trigger)
    except Exception:  # noqa: BLE001 — scheduler pode estar em teardown
        _log.exception("digest_poll_reschedule_failed")
        return last_cron

    _log.info("digest_rescheduled", cron=settings.digest_cron)
    return settings.digest_cron


def _add_digest_poll_job(
    scheduler: BlockingScheduler, settings: settings_store.Settings, tz: ZoneInfo
) -> None:
    """Adiciona o job de polling que reage a mudanças de `digest_cron` no DB (KUBO-44).

    O `last_cron` vive numa closure mutável (`state`) — o job do APScheduler recebe a
    closure por captura. Em testes, `_check_and_reschedule_digest` é testada isoladamente.
    """
    state: dict[str, str] = {"last_cron": settings.digest_cron}

    def _poll() -> None:
        try:
            with client.connect(client.config()) as db:
                state["last_cron"] = _check_and_reschedule_digest(
                    db, scheduler, tz, state["last_cron"]
                )
        except Exception:  # noqa: BLE001 — poll nunca derruba o scheduler
            _log.exception("digest_poll_failed")

    scheduler.add_job(
        _poll,
        trigger=IntervalTrigger(minutes=_DIGEST_POLL_MINUTES),
        id="digest_poll",
    )


def build_scheduler(
    schedules: Schedules,
    settings: settings_store.Settings | None = None,
) -> BlockingScheduler:
    """Monta o BlockingScheduler com um job cron por entry do YAML, mais `digest` e `digest_poll`
    quando `settings` é fornecido.

    Valida TUDO eagerly (falha alta antes do start, não horas depois no 1º disparo). Cada
    entry é `WorkerEntry` (ADR-0010) ou `SweepEntry` (#108/#110) — o dispatch por `isinstance`
    mantém as duas validações/fiações SEPARADAS (`_add_worker_job`/`_add_sweep_job`), cada falha
    vira `ConfigError` (padrão de domínio)."""
    tz = ZoneInfo(schedules.timezone)
    scheduler = BlockingScheduler(timezone=tz)
    for entry in schedules.schedules:
        if isinstance(entry, WorkerEntry):
            _add_worker_job(scheduler, entry, tz)
        else:
            _add_sweep_job(scheduler, entry, tz)
    if settings is not None:
        _add_digest_job(scheduler, settings, tz)
        _add_digest_poll_job(scheduler, settings, tz)
    return scheduler


def make_sigterm_handler(scheduler: BlockingScheduler) -> Callable[[int, Any], None]:
    """Handler de SIGTERM: shutdown(wait=True) — a run em voo termina antes de sair."""

    def _handler(signum: int, frame: Any) -> None:
        _log.info("scheduler_sigterm")
        scheduler.shutdown(wait=True)

    return _handler


def main() -> None:
    """Sobe o agendador: carrega schedules, lê settings, monta jobs, SIGTERM, bloqueia."""
    schedules = load_schedules()
    with client.connect(client.config()) as db:
        settings = settings_store.get_settings(db)
    if settings is None:
        raise ConfigError("settings não encontrado — rode as migrations e o seed")
    # Valida o cron do digest antes de subir (fail-fast no boot).
    _digest_trigger(settings.digest_cron, ZoneInfo(schedules.timezone))
    scheduler = build_scheduler(schedules, settings)
    signal.signal(signal.SIGTERM, make_sigterm_handler(scheduler))
    _log.info(
        "scheduler_starting",
        yaml_jobs=len(schedules.schedules),
        digest_cron=settings.digest_cron,
        timezone=schedules.timezone,
    )
    scheduler.start()
