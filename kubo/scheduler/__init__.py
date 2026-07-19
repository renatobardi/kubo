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
from kubo.runtime.flow_runner import _FLOW_REGISTRY, run_flow
from kubo.runtime.flow_templates import load_flow_templates
from kubo.runtime.runner import run_worker
from kubo.scheduler.sweep import SWEEP_DISPATCH
from kubo.store import client
from kubo.store.knowledge import active_sources
from kubo.workers.digest import DigestWorker
from kubo.workers.distiller import DistillerWorker
from kubo.workers.registry import WORKER_REGISTRY

_REPO_ROOT = Path(__file__).parents[2]
_SCHEDULES_PATH = _REPO_ROOT / "schedules.yaml"
_DESTINATIONS_PATH = _REPO_ROOT / "destinations.yaml"
_TEMPLATES_DIR = _REPO_ROOT / "catalogs" / "flow_templates"
_log = structlog.get_logger()

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


class FlowEntry(BaseModel):
    """Uma entrada agendada de FLOW (sessão 0021, marco 21.4, ADR-0022): dispara
    `run_flow(template_name=flow, question=question, worker_config=config)` no cron. `question`
    é obrigatória (é o que `run_flow` grava em `flow.question`) — sem gate/executor/destination,
    o flow `pipeline` não precisa de nenhum dos três."""

    model_config = ConfigDict(extra="forbid")
    flow: str
    cron: str
    question: str
    config: dict[str, Any] = {}


class SweepEntry(BaseModel):
    """Uma entrada de SWEEP de coleta (#108, ADR-0025 §4): varre os Cadastros ativos de um
    `kind` e dispara um run por Cadastro. `sweep` é o KIND a varrer (dado — `rss`), não um nome
    de worker (o mapa kind→worker é fixo em código, `SWEEP_DISPATCH`). Sem `config`: a config de
    cada run vem do Cadastro, não da entry. Campo obrigatório disjunto (`sweep`) desambigua a
    união sem discriminador, como `worker` e `flow`."""

    model_config = ConfigDict(extra="forbid")
    sweep: str
    cron: str


class Schedules(BaseModel):
    """Config validada de `schedules.yaml`: timezone obrigatória + lista de entries — cada
    entry é `WorkerEntry`, `FlowEntry` OU `SweepEntry` (união em modo smart do Pydantic v2: os
    campos obrigatórios disjuntos `worker` vs `flow`+`question` vs `sweep` bastam para
    desambiguar sem `discriminator=` explícito, ADR-0022/ADR-0025)."""

    model_config = ConfigDict(extra="forbid")
    timezone: str
    schedules: list[WorkerEntry | FlowEntry | SweepEntry]

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


def execute_flow_job(template_name: str, question: str, worker_config: dict[str, Any]) -> None:
    """Executa um FLOW agendado (sessão 0021, marco 21.4) com conexão POR execução — espelho
    exato de `execute_job`, mas delega a `run_flow` em vez de `run_worker` direto (`run_flow` já
    faz o bookkeeping de grafo: instantiate_flow/create_task/transition/run_worker/transition)."""
    try:
        with client.connect(client.config()) as db:
            run_flow(
                db,
                template_name=template_name,
                question=question,
                base_url="",
                worker_config=worker_config,
            )
    except Exception:  # noqa: BLE001 — mesmo tratamento de `execute_job`
        _log.exception("scheduler_flow_job_failed", template=template_name)
        raise


def execute_sweep_job(kind: str) -> None:
    """Executa o sweep de coleta de um `kind` (#108, ADR-0025 §4): lê os Cadastros ATIVOS e
    dispara UM run por Cadastro (preserva 'um run = um feed', ADR-0009). Loop query→run_worker,
    ISOLADO por Cadastro — a falha de um NÃO condena os demais (`try`/`continue`), e cada run
    abre a PRÓPRIA conexão para que um ws morto num Cadastro não derrube o resto do sweep
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


def _add_flow_job(scheduler: BlockingScheduler, entry: FlowEntry, tz: ZoneInfo) -> None:
    """Valida e registra o job de uma `FlowEntry` (marco 21.4): template no catálogo,
    trigger `scheduled` declarado (só flows pensados pra cron podem ser agendados —
    `dev-mini`/`analysis*` são `triggers: [manual]`, humano-gated, e cron desatendido
    neles é a categoria do invariante 5 do CLAUDE.md), behavior no `_FLOW_REGISTRY`,
    cron parseável e `config` coerente com `FlowBehavior.config_model` (quando
    declarado) — mesmo padrão eager de `_add_worker_job`."""
    templates = load_flow_templates(_TEMPLATES_DIR)
    if entry.flow not in templates:
        raise ConfigError(f"template de flow '{entry.flow}' não existe no catálogo")
    template = templates[entry.flow]
    if "scheduled" not in template.triggers:
        raise ConfigError(
            f"template de flow '{entry.flow}' não declara o trigger 'scheduled' "
            f"(triggers={template.triggers!r}) — não pode ser agendado no cron"
        )
    behavior = _FLOW_REGISTRY.get(entry.flow)
    if behavior is None:
        raise ConfigError(f"flow '{entry.flow}' sem handler no FLOW_REGISTRY")
    trigger = _cron_trigger(entry.cron, tz, label=f"flow '{entry.flow}'")
    if behavior.config_model is not None:
        try:
            behavior.config_model.model_validate(entry.config)
        except ValidationError as exc:
            raise ConfigError(
                f"config inválida para flow '{entry.flow}': {format_validation_error(exc)}"
            ) from exc
    scheduler.add_job(
        execute_flow_job,
        trigger=trigger,
        kwargs={
            "template_name": entry.flow,
            "question": entry.question,
            "worker_config": entry.config,
        },
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


def build_scheduler(schedules: Schedules) -> BlockingScheduler:
    """Monta o BlockingScheduler com um job cron por entry, tz explícita SEMPRE.

    Valida TUDO eagerly (falha alta antes do start, não horas depois no 1º disparo). Cada
    entry é `WorkerEntry` (ADR-0010), `FlowEntry` (marco 21.4) ou `SweepEntry` (#108) — o
    dispatch por `isinstance` mantém as três validações/fiações SEPARADAS (`_add_worker_job`/
    `_add_flow_job`/`_add_sweep_job`), cada falha vira `ConfigError` (padrão de domínio)."""
    tz = ZoneInfo(schedules.timezone)
    scheduler = BlockingScheduler(timezone=tz)
    for entry in schedules.schedules:
        if isinstance(entry, WorkerEntry):
            _add_worker_job(scheduler, entry, tz)
        elif isinstance(entry, FlowEntry):
            _add_flow_job(scheduler, entry, tz)
        else:
            _add_sweep_job(scheduler, entry, tz)
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
