"""Testes do agendador `kubo/scheduler/` (ADR-0010).

Cobre o loader de `schedules.yaml` (Pydantic, `extra="forbid"`, timezone
obrigatória e validada via `zoneinfo`), a construção do `BlockingScheduler`
(um job por entry, registry hardcoded, propagação de erro de config/cron), o
handler de SIGTERM e a fiação de `execute_job` (conexão-por-job → `run_worker`,
integração real com SurrealDB). Fecha também o critério de aceite "dispara de
verdade": um `BlockingScheduler` real executando um job dentro do teto.

Imports de `kubo.scheduler` são feitos DENTRO de cada teste (não no topo do
módulo): o pacote ainda não existe (RED do TDD) — importar no topo faria a
suíte inteira falhar por erro de coleta em vez de reportar, teste a teste,
qual comportamento falta.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, ValidationError

from kubo.contracts.models import RunResult, SourcePayload
from kubo.contracts.worker import RunContext, WorkerManifest
from kubo.errors import ConfigError
from kubo.store import client, migrations

_JOB_DB = "test_scheduler_job"


class _FakeConfig(BaseModel):
    """Schema mínimo de config do worker fake usado no teste de execute_job."""

    feed_url: str


class _FakeWorker:
    """Worker conforme, mínimo: devolve uma source, não toca a store."""

    manifest = WorkerManifest(name="fake-sched", version="0.1.0", config=_FakeConfig)

    def run(self, ctx: RunContext) -> RunResult:
        source = SourcePayload(kind="test", canonical="sched://x")
        return RunResult(payloads=[source])


@pytest.fixture
def scheduler_db() -> Iterator[Any]:
    """Database próprio do teste de execute_job, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_JOB_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_JOB_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_JOB_DB};")


# ---------------------------------------------------------------------------
# Loader: Schedules/WorkerEntry (unit, sem DB)
# ---------------------------------------------------------------------------


def test_valid_schedules_dict_parses() -> None:
    """Dict válido (timezone + uma entry) parseia com os campos corretos."""
    from kubo.scheduler import Schedules, WorkerEntry

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [
            {"worker": "feed", "cron": "0 8 * * *", "config": {"feed_url": "https://x/feed"}}
        ],
    }

    parsed = Schedules.model_validate(data)

    assert parsed.timezone == "America/Sao_Paulo"
    entry = parsed.schedules[0]
    assert isinstance(entry, WorkerEntry)
    assert entry.worker == "feed"
    assert entry.cron == "0 8 * * *"
    assert entry.config == {"feed_url": "https://x/feed"}


def test_missing_timezone_is_rejected() -> None:
    """`timezone` é obrigatório (ADR-0010 item II) — ausência é erro de validação."""
    from kubo.scheduler import Schedules

    with pytest.raises(ValidationError):
        Schedules.model_validate({"schedules": []})


def test_extra_top_level_key_is_rejected() -> None:
    """`extra="forbid"` no topo: campo desconhecido é rejeitado na borda."""
    from kubo.scheduler import Schedules

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [],
        "unexpected": "nope",
    }

    with pytest.raises(ValidationError):
        Schedules.model_validate(data)


def test_extra_key_inside_entry_is_rejected() -> None:
    """`extra="forbid"` também dentro de cada entry de `schedules`."""
    from kubo.scheduler import Schedules

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [
            {
                "worker": "feed",
                "cron": "0 8 * * *",
                "config": {},
                "unexpected": "nope",
            }
        ],
    }

    with pytest.raises(ValidationError):
        Schedules.model_validate(data)


def test_unknown_timezone_is_rejected() -> None:
    """Timezone que não existe em `zoneinfo` é rejeitada (não vira surpresa em runtime)."""
    from kubo.scheduler import Schedules

    data = {
        "timezone": "Mars/Phobos",
        "schedules": [{"worker": "feed", "cron": "0 8 * * *", "config": {}}],
    }

    with pytest.raises(ValidationError):
        Schedules.model_validate(data)


def test_load_schedules_reads_real_repo_config() -> None:
    """`load_schedules()` sobre o `schedules.yaml` real da raiz: 6 feeds reais
    (critério de aceite do plano 0005) + 1 entry do destilador diário ATIVO (marco
    8.7, ADR-0013 §VIII) + 1 entry do digest diário (ADR-0015) + 1 entry do flow
    `pipeline` (D51/D52/D54, sessão 0021) — 9 entries no total, timezone explícita,
    cada feed aponta pro worker `feed` com uma URL http(s) não-vazia, e
    destilador/digest/pipeline rodam 1x/dia."""
    from kubo.scheduler import FlowEntry, WorkerEntry, load_schedules

    schedules = load_schedules()
    worker_entries = [e for e in schedules.schedules if isinstance(e, WorkerEntry)]
    flow_entries = [e for e in schedules.schedules if isinstance(e, FlowEntry)]

    assert schedules.timezone == "America/Sao_Paulo"
    assert len(schedules.schedules) == 9
    feed_entries = [e for e in worker_entries if e.worker == "feed"]
    assert len(feed_entries) == 6
    for entry in feed_entries:
        feed_url = entry.config["feed_url"]
        assert isinstance(feed_url, str)
        assert feed_url != ""
        assert urlsplit(feed_url).scheme in ("http", "https")

    # O destilador foi REATIVADO (entry descomentado, mini-sessão 0008 pós-filtro de
    # content vazio); `max_items` subiu de 20 para 50 (D56) pro funil não represar
    # com o volume novo do pipeline.
    distiller_entries = [e for e in worker_entries if e.worker == "distiller"]
    assert len(distiller_entries) == 1
    assert distiller_entries[0].config == {"max_items": 50}

    # O digest diário (ADR-0015): um job às 09:30, após a destilação das 09:00.
    digest_entries = [e for e in worker_entries if e.worker == "digest"]
    assert len(digest_entries) == 1
    assert digest_entries[0].cron == "30 9 * * *"

    # O pipeline (D51/D52/D54): fora do trem 08:00-09:30, `since` congelado (D52).
    assert len(flow_entries) == 1
    assert flow_entries[0].flow == "pipeline"
    assert flow_entries[0].cron == "0 7 * * *"
    assert "since" in flow_entries[0].config


def test_distiller_entry_config_validates() -> None:
    """O entry ATIVO do destilador (`max_items: 50`, D56) valida contra o schema do
    DistillerWorker — o config declarado no schedules.yaml casa com o contrato do
    worker, então o scheduler o instancia sem surpresa em runtime."""
    from kubo.scheduler import WorkerEntry
    from kubo.workers.distiller import DistillerConfig, DistillerWorker

    entry = WorkerEntry(worker="distiller", cron="0 9 * * *", config={"max_items": 50})

    assert DistillerWorker.manifest.config is DistillerConfig
    DistillerWorker.manifest.config.model_validate(entry.config)  # não levanta = válido


# ---------------------------------------------------------------------------
# build_scheduler (unit, sem DB)
# ---------------------------------------------------------------------------


def test_build_scheduler_creates_one_job_per_entry() -> None:
    """Um job por entry do `schedules.yaml` real — 6 feeds + 1 destilador diário
    (reativado na mini-sessão 0008) + 1 digest diário (ADR-0015) + 1 flow `pipeline`
    (sessão 0021) = 9 jobs. Não inicia o scheduler (`.start()` bloquearia o teste)."""
    from kubo.scheduler import build_scheduler, load_schedules

    scheduler = build_scheduler(load_schedules())

    assert isinstance(scheduler, BlockingScheduler)
    assert len(scheduler.get_jobs()) == 9


def test_build_scheduler_rejects_unknown_worker() -> None:
    """Worker fora do `WORKER_REGISTRY` levanta `ConfigError` — sem descoberta
    dinâmica (ADR-0010 item III), `KeyError` vira falha explícita de config."""
    from kubo.scheduler import Schedules, WorkerEntry, build_scheduler

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[WorkerEntry(worker="ghost", cron="0 8 * * *", config={})],
    )

    with pytest.raises(ConfigError):
        build_scheduler(schedules)


def test_build_scheduler_rejects_bad_cron_as_config_error() -> None:
    """Cron malformado vira `ConfigError` (padrão de domínio), não a exceção crua da
    lib — falha alta e legível antes do start, coerente com o resto do módulo."""
    from kubo.scheduler import Schedules, WorkerEntry, build_scheduler

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[
            WorkerEntry(worker="feed", cron="not a cron", config={"feed_url": "https://x/f"})
        ],
    )

    with pytest.raises(ConfigError, match="cron"):
        build_scheduler(schedules)


def test_build_scheduler_rejects_invalid_worker_config() -> None:
    """Config incoerente com o schema do worker (ex.: feed sem `feed_url`) vira
    `ConfigError` no BUILD — falha alta no start, não horas depois no 1º disparo do cron."""
    from kubo.scheduler import Schedules, WorkerEntry, build_scheduler

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[WorkerEntry(worker="feed", cron="0 8 * * *", config={"wrong": "field"})],
    )

    with pytest.raises(ConfigError, match="config"):
        build_scheduler(schedules)


# ---------------------------------------------------------------------------
# _instantiate: constrói worker + dependências por nome (marco 8.7, ADR-0013 §VIII)
# ---------------------------------------------------------------------------


def test_instantiate_distiller_builds_worker_with_executor_and_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_instantiate("distiller")` monta o `DistillerWorker` com executor (Groq,
    modelo pinado) + `GeminiEmbedder` lido da env — `from_env` só LÊ a env
    (não valida a key), então uma key fake já constrói."""
    from kubo.embedding import GeminiEmbedder
    from kubo.scheduler import _instantiate
    from kubo.workers.distiller import DistillerWorker

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-teste")

    worker, embedder = _instantiate("distiller")

    assert isinstance(worker, DistillerWorker)
    assert isinstance(embedder, GeminiEmbedder)


def test_instantiate_feed_builds_worker_without_embedder() -> None:
    """`_instantiate("feed")` devolve o `FeedWorker` sem embedder (só o
    destilador precisa de um)."""
    from kubo.scheduler import _instantiate
    from kubo.workers.feed import FeedWorker

    worker, embedder = _instantiate("feed")

    assert isinstance(worker, FeedWorker)
    assert embedder is None


# ---------------------------------------------------------------------------
# make_sigterm_handler (unit, sem DB)
# ---------------------------------------------------------------------------


def test_sigterm_handler_shuts_down_scheduler_waiting_for_inflight_job() -> None:
    """SIGTERM chama `scheduler.shutdown(wait=True)` — deixa a job em voo
    terminar antes de sair (ADR-0010 item IV), nunca um shutdown abrupto."""
    from kubo.scheduler import make_sigterm_handler

    mock_scheduler = MagicMock()
    handler = make_sigterm_handler(mock_scheduler)

    handler(15, None)

    mock_scheduler.shutdown.assert_called_once_with(wait=True)


# ---------------------------------------------------------------------------
# execute_job: conexão-por-job -> run_worker (integração, SurrealDB)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_execute_job_opens_own_connection_and_drives_run_worker(
    scheduler_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`execute_job` abre a PRÓPRIA conexão (ADR-0010 item V) e roda o worker
    via `run_worker`: um `run` fecha em 'ok' e a source entregue é persistida."""
    from kubo import scheduler

    monkeypatch.setitem(scheduler.WORKER_REGISTRY, "fake", _FakeWorker)
    job_cfg = replace(client.config(), database=_JOB_DB)
    monkeypatch.setattr(scheduler.client, "config", lambda: job_cfg)

    scheduler.execute_job("fake", {"feed_url": "https://x/feed"})

    runs = scheduler_db.query("SELECT status FROM run;")
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
    assert scheduler_db.query("SELECT count() FROM source GROUP ALL;")[0]["count"] == 1


# ---------------------------------------------------------------------------
# Critério de aceite "dispara de verdade": BlockingScheduler real, sem DB
# ---------------------------------------------------------------------------


def test_blocking_scheduler_fires_a_job_within_generous_margin() -> None:
    """Prova empírica de que o agendador dispara de verdade: um job com
    `IntervalTrigger` de 1s roda dentro de um teto de 10s — folga generosa
    contra CI carregado. Teardown incondicional (try/finally) para não deixar
    thread solta mesmo se a asserção falhar."""
    fired = threading.Event()
    scheduler = BlockingScheduler()
    scheduler.add_job(fired.set, trigger=IntervalTrigger(seconds=1))
    thread = threading.Thread(target=scheduler.start, daemon=True)
    thread.start()
    try:
        assert fired.wait(timeout=10), "job não disparou dentro do teto de 10s"
    finally:
        scheduler.shutdown(wait=False)
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Schedules.schedules vira união WorkerEntry|FlowEntry (sessão 0021, marco 21.4,
# ADR-0021 §21.3/21.4): flows agendados (`pipeline`) ao lado de workers.
# ---------------------------------------------------------------------------


def test_worker_entry_dict_parses_to_worker_entry_instance() -> None:
    """Uma entry `{worker, cron, config}` parseia para `WorkerEntry` (shape existente,
    ADR-0010 item II) — a união não muda o comportamento já coberto."""
    from kubo.scheduler import Schedules, WorkerEntry  # type: ignore[attr-defined]

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [
            {"worker": "feed", "cron": "0 8 * * *", "config": {"feed_url": "https://x/feed"}}
        ],
    }

    parsed = Schedules.model_validate(data)

    assert isinstance(parsed.schedules[0], WorkerEntry)


def test_flow_entry_dict_parses_to_flow_entry_instance() -> None:
    """Uma entry `{flow, cron, question, config}` parseia para `FlowEntry` (novo shape,
    marco 21.4) — o mesmo `Schedules.schedules` aceita os dois tipos sem discriminador
    explícito (campos obrigatórios disjuntos: `worker` vs `flow`+`question`)."""
    from kubo.scheduler import FlowEntry, Schedules  # type: ignore[attr-defined]

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [
            {
                "flow": "pipeline",
                "cron": "0 7 * * *",
                "question": "coleta diária",
                "config": {"since": "2026-07-16T00:00:00Z"},
            }
        ],
    }

    parsed = Schedules.model_validate(data)

    entry = parsed.schedules[0]
    assert isinstance(entry, FlowEntry)
    assert entry.flow == "pipeline"  # type: ignore[attr-defined]
    assert entry.question == "coleta diária"  # type: ignore[attr-defined]
    assert entry.config == {"since": "2026-07-16T00:00:00Z"}


def test_entry_with_both_worker_and_flow_keys_is_rejected() -> None:
    """Uma entry ambígua com `worker` E `flow` presentes falha em AMBOS os ramos da união
    (`extra="forbid"` em cada modelo barra o campo do outro) — `Schedules.model_validate`
    levanta `ValidationError`, nunca escolhe um dos dois silenciosamente."""
    from kubo.scheduler import Schedules

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [
            {
                "worker": "feed",
                "flow": "pipeline",
                "cron": "0 7 * * *",
                "question": "q",
                "config": {},
            }
        ],
    }

    with pytest.raises(ValidationError):
        Schedules.model_validate(data)


def test_flow_entry_missing_question_is_rejected() -> None:
    """`question` é campo obrigatório de `FlowEntry` (é o que `run_flow` grava no
    `flow.question` — sem ele o flow não tem o que perguntar)."""
    from kubo.scheduler import Schedules

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [{"flow": "pipeline", "cron": "0 7 * * *", "config": {}}],
    }

    with pytest.raises(ValidationError):
        Schedules.model_validate(data)


def test_real_schedules_yaml_loads_the_worker_flow_mix() -> None:
    """Regressão: o `schedules.yaml` real da raiz (8 worker entries + 1 flow entry do
    pipeline, D51/D52/D54/D56, sessão 0021) carrega sem erro — a união WorkerEntry|FlowEntry
    desambigua os dois tipos sem `discriminator=` explícito."""
    from kubo.scheduler import FlowEntry, WorkerEntry, load_schedules

    schedules = load_schedules()

    assert sum(isinstance(e, WorkerEntry) for e in schedules.schedules) == 8
    assert sum(isinstance(e, FlowEntry) for e in schedules.schedules) == 1


# ---------------------------------------------------------------------------
# build_scheduler: validação eager de FlowEntry (registry de template, cron,
# config_model do FlowBehavior) — mesmo padrão já usado para WorkerEntry.
# ---------------------------------------------------------------------------


def test_build_scheduler_rejects_unknown_flow_template() -> None:
    """`flow` que não é um template real do catálogo → `ConfigError` no BUILD, antes do
    scheduler subir (não no 1º disparo do cron)."""
    from kubo.scheduler import FlowEntry, Schedules, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[
            FlowEntry(flow="ghost-flow-template", cron="0 7 * * *", question="q", config={})
        ],
    )

    with pytest.raises(ConfigError):
        build_scheduler(schedules)


def test_build_scheduler_rejects_flow_without_scheduled_trigger() -> None:
    """`dev-mini` é um template REAL (existe no catálogo, tem handler no
    `_FLOW_REGISTRY`) mas declara `triggers: [manual]` (ADR-0016/ADR-0019 §V: pensado
    pra disparo humano via CLI/browser, abre um gate de PR e gasta `budget_usd` real).
    Uma `FlowEntry` de cron pra ele deve ser barrada eagerly no BUILD — sem este check,
    um typo/copy-paste em `schedules.yaml` ligaria um flow humano-gated a cron
    desatendido (categoria do invariante 5 do CLAUDE.md: gate humano obrigatório)."""
    from kubo.scheduler import FlowEntry, Schedules, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[FlowEntry(flow="dev-mini", cron="0 7 * * *", question="q", config={})],
    )

    with pytest.raises(ConfigError, match="triggers|scheduled"):
        build_scheduler(schedules)


def test_build_scheduler_rejects_bad_cron_for_flow_entry() -> None:
    """Cron malformado numa `FlowEntry` também vira `ConfigError` (padrão de domínio já
    aplicado a `WorkerEntry` — `test_build_scheduler_rejects_bad_cron_as_config_error`)."""
    from kubo.scheduler import FlowEntry, Schedules, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[
            FlowEntry(
                flow="pipeline",
                cron="not a cron",
                question="q",
                config={"since": "2026-07-16T00:00:00Z"},
            )
        ],
    )

    with pytest.raises(ConfigError, match="cron"):
        build_scheduler(schedules)


def test_build_scheduler_rejects_invalid_flow_config() -> None:
    """`config` incoerente com `FlowBehavior.config_model` (`pipeline` exige `since`, ver
    `kubo/workers/github_releases.py:GithubReleasesConfig`) vira `ConfigError` no BUILD —
    mesmo padrão eager já aplicado a `worker_cls.manifest.config.model_validate`."""
    from kubo.scheduler import FlowEntry, Schedules, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[FlowEntry(flow="pipeline", cron="0 7 * * *", question="q", config={})],
    )

    with pytest.raises(ConfigError, match="config"):
        build_scheduler(schedules)


def test_build_scheduler_accepts_valid_flow_entry_and_wires_one_job() -> None:
    """Uma `FlowEntry` com `config` válido não levanta — o scheduler monta com o job
    correspondente."""
    from kubo.scheduler import FlowEntry, Schedules, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[
            FlowEntry(
                flow="pipeline",
                cron="0 7 * * *",
                question="coleta diária",
                config={"since": "2026-07-16T00:00:00Z"},
            )
        ],
    )

    scheduler = build_scheduler(schedules)

    assert len(scheduler.get_jobs()) == 1


def test_build_scheduler_wires_flow_entry_to_execute_flow_job() -> None:
    """O job de uma `FlowEntry` chama `execute_flow_job` (não `execute_job`, que é só pra
    `WorkerEntry`), com `template_name`/`question`/`worker_config` derivados da entry —
    espelha `test_execute_job_opens_own_connection_and_drives_run_worker` na intenção, mas
    aqui só a FIAÇÃO importa (o comportamento de `run_flow` já está coberto pelas
    integrações de `tests/runtime/test_flow_pipeline_vertical.py`)."""
    from kubo.scheduler import (
        FlowEntry,  # type: ignore[attr-defined]
        Schedules,
        build_scheduler,
        execute_flow_job,  # type: ignore[attr-defined]
    )

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[
            FlowEntry(
                flow="pipeline",
                cron="0 7 * * *",
                question="coleta diária",
                config={"since": "2026-07-16T00:00:00Z"},
            )
        ],
    )

    scheduler = build_scheduler(schedules)
    job = scheduler.get_jobs()[0]

    assert job.func is execute_flow_job
    assert job.kwargs == {
        "template_name": "pipeline",
        "question": "coleta diária",
        "worker_config": {"since": "2026-07-16T00:00:00Z"},
    }


@pytest.mark.integration
@respx.mock
def test_execute_flow_job_opens_own_connection_and_drives_run_flow(
    scheduler_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Achado do CodeRabbit (PR #57): a fiação (`test_build_scheduler_wires_flow_entry_to_
    execute_flow_job` acima) só prova QUE `execute_flow_job` é o alvo do job, nunca CHAMA a
    função de verdade. Este teste espelha `test_execute_job_opens_own_connection_and_drives_
    run_worker`: chama `execute_flow_job` diretamente contra o template `pipeline` REAL, com
    a rede do GitHub mockada (respx) — prova que abre a PRÓPRIA conexão (ADR-0010 item V) e
    que `run_flow` recebe `template_name`/`question`/`base_url`/`worker_config` corretos ao
    ponto de completar o flow de ponta a ponta (flow/task/run persistidos, task em `stored`)."""
    from kubo import scheduler

    monkeypatch.setenv("GITHUB_TOKEN_WATCH", "fake-watch-token")  # pragma: allowlist secret
    job_cfg = replace(client.config(), database=_JOB_DB)
    monkeypatch.setattr(scheduler.client, "config", lambda: job_cfg)

    respx.get("https://api.github.com/user/subscriptions").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "full_name": "acme/widget"}])
    )
    respx.get("https://api.github.com/repos/acme/widget/releases").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "tag_name": "v1.0.0",
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-07-01T00:00:00Z",
                }
            ],
        )
    )

    scheduler.execute_flow_job("pipeline", "coleta diária", {"since": "2026-06-01T00:00:00Z"})

    tasks = scheduler_db.query("SELECT state FROM task;")
    assert len(tasks) == 1
    assert tasks[0]["state"] == "stored"
    runs = scheduler_db.query("SELECT status FROM run;")
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"


@pytest.mark.integration
def test_execute_flow_job_reraises_and_logs_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`execute_flow_job` NÃO engole falha de setup (template inexistente, aqui) — repropaga
    depois de logar, mesmo tratamento de `execute_job` (`scheduler_flow_job_failed`). Marcado
    integration: `client.connect(client.config())` abre conexão real ANTES do `run_flow`
    levantar `ConfigError` (o guard de template inexistente não toca `db`, mas a conexão em
    si já foi aberta pelo `with`)."""
    from kubo import scheduler

    job_cfg = replace(client.config(), database=_JOB_DB)
    monkeypatch.setattr(scheduler.client, "config", lambda: job_cfg)

    with pytest.raises(ConfigError, match="não existe no catálogo"):
        scheduler.execute_flow_job("ghost-template", "q", {})
