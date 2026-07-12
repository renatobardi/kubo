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

import pytest
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
# Loader: Schedules/ScheduleEntry (unit, sem DB)
# ---------------------------------------------------------------------------


def test_valid_schedules_dict_parses() -> None:
    """Dict válido (timezone + uma entry) parseia com os campos corretos."""
    from kubo.scheduler import Schedules

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [
            {"worker": "feed", "cron": "0 8 * * *", "config": {"feed_url": "https://x/feed"}}
        ],
    }

    parsed = Schedules.model_validate(data)

    assert parsed.timezone == "America/Sao_Paulo"
    entry = parsed.schedules[0]
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
    (critério de aceite do plano 0005) + 1 entry do destilador diário (marco
    8.7, ADR-0013 §VIII) — 7 entries no total, timezone explícita, cada feed
    aponta pro worker `feed` com uma URL http(s) não-vazia."""
    from kubo.scheduler import load_schedules

    schedules = load_schedules()

    assert schedules.timezone == "America/Sao_Paulo"
    assert len(schedules.schedules) == 7
    feed_entries = [e for e in schedules.schedules if e.worker == "feed"]
    assert len(feed_entries) == 6
    for entry in feed_entries:
        feed_url = entry.config["feed_url"]
        assert isinstance(feed_url, str)
        assert feed_url != ""
        assert urlsplit(feed_url).scheme in ("http", "https")

    distiller_entries = [e for e in schedules.schedules if e.worker == "distiller"]
    assert len(distiller_entries) == 1
    assert distiller_entries[0].config == {"max_items": 20}


# ---------------------------------------------------------------------------
# build_scheduler (unit, sem DB)
# ---------------------------------------------------------------------------


def test_build_scheduler_creates_one_job_per_entry() -> None:
    """Um job por entry do `schedules.yaml` real — 6 feeds + 1 destilador, 7
    jobs (marco 8.7). Não inicia o scheduler (`.start()` bloquearia o teste)."""
    from kubo.scheduler import build_scheduler, load_schedules

    scheduler = build_scheduler(load_schedules())

    assert isinstance(scheduler, BlockingScheduler)
    assert len(scheduler.get_jobs()) == 7


def test_build_scheduler_rejects_unknown_worker() -> None:
    """Worker fora do `WORKER_REGISTRY` levanta `ConfigError` — sem descoberta
    dinâmica (ADR-0010 item III), `KeyError` vira falha explícita de config."""
    from kubo.scheduler import ScheduleEntry, Schedules, build_scheduler

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[ScheduleEntry(worker="ghost", cron="0 8 * * *", config={})],
    )

    with pytest.raises(ConfigError):
        build_scheduler(schedules)


def test_build_scheduler_rejects_bad_cron_as_config_error() -> None:
    """Cron malformado vira `ConfigError` (padrão de domínio), não a exceção crua da
    lib — falha alta e legível antes do start, coerente com o resto do módulo."""
    from kubo.scheduler import ScheduleEntry, Schedules, build_scheduler

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[
            ScheduleEntry(worker="feed", cron="not a cron", config={"feed_url": "https://x/f"})
        ],
    )

    with pytest.raises(ConfigError, match="cron"):
        build_scheduler(schedules)


def test_build_scheduler_rejects_invalid_worker_config() -> None:
    """Config incoerente com o schema do worker (ex.: feed sem `feed_url`) vira
    `ConfigError` no BUILD — falha alta no start, não horas depois no 1º disparo do cron."""
    from kubo.scheduler import ScheduleEntry, Schedules, build_scheduler

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[ScheduleEntry(worker="feed", cron="0 8 * * *", config={"wrong": "field"})],
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
