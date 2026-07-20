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
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, ValidationError
from surrealdb import RecordID

from kubo.contracts.models import RunResult, SourcePayload
from kubo.contracts.worker import RunContext, WorkerManifest
from kubo.errors import ConfigError
from kubo.store import client, migrations
from kubo.store.settings import Settings

_JOB_DB = "test_scheduler_job"


def _scheduler_settings(cron: str = "30 9 * * *", paused: bool = False) -> Settings:
    """Settings mínimo para construir o scheduler com o job `digest` em testes unit."""
    return Settings(
        id=RecordID("settings", "global"),
        digest_cron=cron,
        distribution_paused=paused,
        default_destination=None,
    )


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
    """`load_schedules()` sobre o `schedules.yaml` real da raiz, PÓS corte RSS (#108), migração
    do GitHub pro sweep (#110) e KUBO-44 (digest sai do YAML): 1 `sweep: rss` + 1
    `sweep: github-repo` (ADR-0025 §4/§5) + 1 destilador diário ATIVO (ADR-0013 §VIII) —
    3 entries, timezone explícita, ZERO worker `feed` estático, ZERO flow agendado
    (FlowEntry aposentado, #110) e ZERO digest no YAML."""
    from kubo.scheduler import SweepEntry, WorkerEntry, load_schedules

    schedules = load_schedules()
    worker_entries = [e for e in schedules.schedules if isinstance(e, WorkerEntry)]
    sweep_entries = [e for e in schedules.schedules if isinstance(e, SweepEntry)]

    assert schedules.timezone == "America/Sao_Paulo"
    assert len(schedules.schedules) == 3
    # A coleta de feeds virou um sweep dirigido por Cadastro; nenhum worker `feed` estático.
    assert not any(e.worker == "feed" for e in worker_entries)
    assert {e.sweep for e in sweep_entries} == {"rss", "github-repo"}
    rss = next(e for e in sweep_entries if e.sweep == "rss")
    assert rss.cron == "0 8 * * *"

    # O destilador foi REATIVADO (entry descomentado, mini-sessão 0008 pós-filtro de
    # content vazio); `max_items` subiu de 20 para 50 (D56) pro funil não represar.
    distiller_entries = [e for e in worker_entries if e.worker == "distiller"]
    assert len(distiller_entries) == 1
    assert distiller_entries[0].config == {"max_items": 50}

    # O digest diário saiu do schedules.yaml no KUBO-44 (horário vem do settings no DB).
    digest_entries = [e for e in worker_entries if e.worker == "digest"]
    assert not digest_entries

    # O sweep github-repo (#110): fora do trem 08:00-09:30, 07:00, sem config (piso `since` =
    # created_at de cada Cadastro).
    github = next(e for e in sweep_entries if e.sweep == "github-repo")
    assert github.cron == "0 7 * * *"


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


def test_build_scheduler_creates_yaml_digest_and_poll_jobs() -> None:
    """Com settings, o scheduler monta: 3 jobs do YAML (2 sweeps + distiller) + digest + poll = 5.
    Não inicia o scheduler (`.start()` bloquearia)."""
    from kubo.scheduler import build_scheduler, load_schedules

    scheduler = build_scheduler(load_schedules(), _scheduler_settings())

    assert isinstance(scheduler, BlockingScheduler)
    assert len(scheduler.get_jobs()) == 5


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
# Schedules.schedules vira união WorkerEntry|SweepEntry (ADR-0010/ADR-0025). O
# agendamento de FLOW (FlowEntry) foi aposentado no #110 junto com o pipeline.
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


def test_real_schedules_yaml_loads_the_worker_sweep_mix() -> None:
    """Regressão: o `schedules.yaml` real da raiz, pós corte RSS (#108), migração do GitHub pro
    sweep (#110) e KUBO-44 (digest sai do YAML), carrega sem erro — a união WorkerEntry|SweepEntry
    desambigua os dois tipos sem `discriminator=`. Sobram 1 worker (distiller) + 2 sweeps
    (rss, github-repo)."""
    from kubo.scheduler import SweepEntry, WorkerEntry, load_schedules

    schedules = load_schedules()

    assert sum(isinstance(e, WorkerEntry) for e in schedules.schedules) == 1
    assert sum(isinstance(e, SweepEntry) for e in schedules.schedules) == 2


# ---------------------------------------------------------------------------
# SweepEntry (#108, corte RSS do ADR-0025 §4): a coleta de feeds sai da lista
# estática do schedules.yaml e vira um sweep que varre os Cadastros ativos e
# dispara UM run por Cadastro. Terceiro membro da união (worker|flow|sweep).
# ---------------------------------------------------------------------------


class _DummyCtx:
    """Context manager mínimo que devolve um db fake — evita conexão real nos testes unit
    de `execute_sweep_job` (a lógica do loop não toca o banco de verdade)."""

    def __enter__(self) -> Any:
        return MagicMock()

    def __exit__(self, *_: object) -> bool:
        return False


def test_sweep_entry_dict_parses_to_sweep_entry_instance() -> None:
    """Uma entry `{sweep, cron}` parseia para `SweepEntry` (novo shape, #108) — o `sweep` é o
    KIND a varrer (dado, não nome de worker), campo obrigatório disjunto que desambigua a união
    sem discriminador explícito (como `worker` vs `flow`)."""
    from kubo.scheduler import Schedules, SweepEntry  # type: ignore[attr-defined]

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [{"sweep": "rss", "cron": "0 8 * * *"}],
    }

    parsed = Schedules.model_validate(data)

    entry = parsed.schedules[0]
    assert isinstance(entry, SweepEntry)
    assert entry.sweep == "rss"  # type: ignore[attr-defined]
    assert entry.cron == "0 8 * * *"


def test_entry_with_worker_and_sweep_keys_is_rejected() -> None:
    """Entry ambígua com `worker` E `sweep` falha em todos os ramos da união (`extra="forbid"`
    em cada modelo barra o campo do outro) — nunca escolhe um dos dois em silêncio."""
    from kubo.scheduler import Schedules

    data = {
        "timezone": "America/Sao_Paulo",
        "schedules": [{"worker": "feed", "sweep": "rss", "cron": "0 8 * * *"}],
    }

    with pytest.raises(ValidationError):
        Schedules.model_validate(data)


def test_build_scheduler_accepts_valid_sweep_entry_and_wires_one_job() -> None:
    """Uma `SweepEntry` com kind despachável não levanta — o scheduler monta o job do sweep."""
    from kubo.scheduler import Schedules, SweepEntry, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[SweepEntry(sweep="rss", cron="0 8 * * *")],
    )

    scheduler = build_scheduler(schedules)

    assert len(scheduler.get_jobs()) == 1


def test_build_scheduler_rejects_unknown_sweep_kind() -> None:
    """`sweep` de kind sem despacho em `SWEEP_DISPATCH` (ex.: `banana`) vira `ConfigError` no
    BUILD, eager — assim o caso 'kind desconhecido em runtime' fica IMPOSSÍVEL por construção
    (não há skip-silencioso nem falha tardia no 1º disparo do cron)."""
    from kubo.scheduler import Schedules, SweepEntry, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[SweepEntry(sweep="banana", cron="0 8 * * *")],
    )

    with pytest.raises(ConfigError, match="sweep"):
        build_scheduler(schedules)


def test_build_scheduler_rejects_bad_cron_for_sweep_entry() -> None:
    """Cron malformado numa `SweepEntry` também vira `ConfigError` (padrão de domínio já
    aplicado a `WorkerEntry`/`FlowEntry`)."""
    from kubo.scheduler import Schedules, SweepEntry, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[SweepEntry(sweep="rss", cron="not a cron")],
    )

    with pytest.raises(ConfigError, match="cron"):
        build_scheduler(schedules)


def test_build_scheduler_wires_sweep_entry_to_execute_sweep_job() -> None:
    """O job de uma `SweepEntry` chama `execute_sweep_job` com o `kind` da entry — não
    `execute_job`/`execute_flow_job` (fiação separada por isinstance, como os outros dois)."""
    from kubo.scheduler import (  # type: ignore[attr-defined]
        Schedules,
        SweepEntry,
        build_scheduler,
        execute_sweep_job,
    )

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[SweepEntry(sweep="rss", cron="0 8 * * *")],
    )

    job = build_scheduler(schedules).get_jobs()[0]

    assert job.func is execute_sweep_job
    assert job.kwargs == {"kind": "rss"}


def test_execute_sweep_job_dispatches_one_run_per_active_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O critério central do #108: dados N Cadastros ativos, o sweep dispara N runs — um
    `run_worker` por Cadastro (preserva 'um run = um feed', ADR-0009), com a config derivada da
    fonte (canonical→feed_url, title, tags fecham o metadata)."""
    from surrealdb import RecordID

    from kubo import scheduler
    from kubo.store.knowledge import ActiveSource

    _created = datetime(2026, 7, 18, tzinfo=UTC)
    sources = [
        ActiveSource(
            id=RecordID("source", "a"),
            kind="rss",
            canonical="https://a/feed",
            title="A",
            tags=["ai"],
            created_at=_created,
        ),
        ActiveSource(
            id=RecordID("source", "b"),
            kind="rss",
            canonical="https://b/feed",
            title=None,
            tags=[],
            created_at=_created,
        ),
    ]
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(scheduler, "active_sources", lambda db, *, kind: sources)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        scheduler,
        "run_worker",
        lambda db, worker, *, config: calls.append((type(worker).__name__, config)),
    )

    scheduler.execute_sweep_job("rss")

    assert len(calls) == 2
    assert calls[0] == ("FeedWorker", {"feed_url": "https://a/feed", "title": "A", "tags": ["ai"]})
    assert calls[1] == (
        "FeedWorker",
        {"feed_url": "https://b/feed", "title": None, "tags": []},
    )


def test_execute_sweep_job_isolates_failing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolamento por Cadastro: se o run de UM Cadastro falha (ws morto, setup), os demais AINDA
    rodam — o sweep loga e segue, nunca explode a metade restante. Sem isso, um feed ruim
    derrubaria a coleta de todos os seguintes."""
    from surrealdb import RecordID

    from kubo import scheduler
    from kubo.store.knowledge import ActiveSource

    sources = [
        ActiveSource(
            id=RecordID("source", c),
            kind="rss",
            canonical=f"https://{c}/f",
            title=c,
            tags=[],
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
        )
        for c in ("a", "b", "c")
    ]
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(scheduler, "active_sources", lambda db, *, kind: sources)
    attempted: list[str] = []

    def _run(db: Any, worker: Any, *, config: dict[str, Any]) -> None:
        attempted.append(config["feed_url"])
        if config["feed_url"] == "https://b/f":
            raise RuntimeError("ws morreu no meio do sweep")

    monkeypatch.setattr(scheduler, "run_worker", _run)

    scheduler.execute_sweep_job("rss")  # NÃO propaga a falha do 'b'

    assert attempted == ["https://a/f", "https://b/f", "https://c/f"]


def test_sweep_dispatch_includes_github_repo() -> None:
    """#110: o mapa fixo `SWEEP_DISPATCH` ganha a chave `github-repo` → worker
    `GithubReleasesWorker` (kind→worker é código, nunca dado do Cadastro, ADR-0025 §7)."""
    from kubo.scheduler.sweep import SWEEP_DISPATCH
    from kubo.workers.github_releases import GithubReleasesWorker

    assert "github-repo" in SWEEP_DISPATCH
    assert SWEEP_DISPATCH["github-repo"].worker_factory is GithubReleasesWorker


def test_github_repo_config_derives_repo_and_since_from_cadastro() -> None:
    """#110/D2: a config do coletor sai do Cadastro — canonical `https://github.com/owner/name`
    → `repo` `owner/name`; `created_at` do Cadastro → `since` (piso de estreia por-repo, sem
    since global no schedules.yaml)."""
    from surrealdb import RecordID

    from kubo.scheduler.sweep import SWEEP_DISPATCH
    from kubo.store.knowledge import ActiveSource

    created = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    source = ActiveSource(
        id=RecordID("source", "x"),
        kind="github-repo",
        canonical="https://github.com/acme/widget",
        title="Widget",
        tags=[],
        created_at=created,
    )

    config = SWEEP_DISPATCH["github-repo"].build_config(source)

    assert config == {"repo": "acme/widget", "since": created}


def test_build_scheduler_accepts_github_repo_sweep_entry() -> None:
    """`sweep: github-repo` agora é despachável (#110) — o scheduler monta o job sem levantar."""
    from kubo.scheduler import Schedules, SweepEntry, build_scheduler  # type: ignore[attr-defined]

    schedules = Schedules(
        timezone="America/Sao_Paulo",
        schedules=[SweepEntry(sweep="github-repo", cron="0 7 * * *")],
    )

    scheduler = build_scheduler(schedules)

    assert len(scheduler.get_jobs()) == 1


def test_execute_sweep_job_dispatches_github_repo_with_repo_and_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O sweep `github-repo` dispara UM `run_worker(GithubReleasesWorker, config)` por Cadastro
    ativo, com a config derivada da fonte (repo da canonical, since do created_at) — simétrico ao
    sweep `rss`, "um run = um Cadastro" (#110, ADR-0025 §4/§5)."""
    from surrealdb import RecordID

    from kubo import scheduler
    from kubo.store.knowledge import ActiveSource

    created = datetime(2026, 7, 18, tzinfo=UTC)
    sources = [
        ActiveSource(
            id=RecordID("source", "x"),
            kind="github-repo",
            canonical="https://github.com/acme/widget",
            title="Widget",
            tags=[],
            created_at=created,
        )
    ]
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(scheduler, "active_sources", lambda db, *, kind: sources)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        scheduler,
        "run_worker",
        lambda db, worker, *, config: calls.append((type(worker).__name__, config)),
    )

    scheduler.execute_sweep_job("github-repo")

    assert calls == [("GithubReleasesWorker", {"repo": "acme/widget", "since": created})]


@pytest.mark.integration
def test_execute_sweep_job_honors_active_filter_against_real_db(
    scheduler_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fiação real (ADR-0010 item V): `execute_sweep_job` abre a PRÓPRIA conexão, lê os ativos do
    banco de verdade e despacha só por eles. Semeia 2 rss ativos + 1 pausado + 1 arquivado + 1
    github-repo ativo; o sweep de `rss` dispara run só pelos 2 ativos (pausado/arquivado/outro
    kind = 0 runs). `run_worker` é mockado para não puxar a rede do FeedWorker — a persistência
    da run já é coberta por `test_execute_job_*`; aqui importa o FILTRO."""
    from kubo import scheduler
    from kubo.store import knowledge

    job_cfg = replace(client.config(), database=_JOB_DB)
    monkeypatch.setattr(scheduler.client, "config", lambda: job_cfg)
    knowledge.create_source(scheduler_db, kind="rss", canonical="https://a.test/feed", title="A")
    knowledge.create_source(scheduler_db, kind="rss", canonical="https://b.test/feed", title="B")
    paused = knowledge.create_source(scheduler_db, kind="rss", canonical="https://p.test/feed")
    knowledge.set_source_enabled(scheduler_db, id=paused, enabled=False)
    archived = knowledge.create_source(scheduler_db, kind="rss", canonical="https://x.test/feed")
    knowledge.archive_source(scheduler_db, id=archived)
    knowledge.create_source(scheduler_db, kind="github-repo", canonical="https://github.com/o/r")
    dispatched: list[str] = []
    monkeypatch.setattr(
        scheduler, "run_worker", lambda db, worker, *, config: dispatched.append(config["feed_url"])
    )

    scheduler.execute_sweep_job("rss")

    assert sorted(dispatched) == ["https://a.test/feed", "https://b.test/feed"]


# ---------------------------------------------------------------------------
# Settings / digest / poll (KUBO-44, ADR-0028)
# ---------------------------------------------------------------------------


def _fake_db() -> Any:
    """Db fake mínimo — `get_settings` é injetada pelo monkeypatch do teste."""
    return object()


def test_build_scheduler_digest_job_uses_settings_cron() -> None:
    """O job `digest` é registrado com o cron do settings, não do schedules.yaml."""
    from apscheduler.triggers.cron import CronTrigger

    from kubo.scheduler import Schedules, SweepEntry, build_scheduler

    schedules = Schedules(
        timezone="America/Sao_Paulo", schedules=[SweepEntry(sweep="rss", cron="0 8 * * *")]
    )
    scheduler = build_scheduler(schedules, _scheduler_settings(cron="0 15 * * *"))

    digest_job = scheduler.get_job("digest")
    assert digest_job is not None
    assert isinstance(digest_job.trigger, CronTrigger)
    assert digest_job.kwargs == {"worker_name": "digest", "config": {}}


def test_build_scheduler_poll_job_is_interval() -> None:
    """Além do digest, build_scheduler adiciona um job de poll com IntervalTrigger de 5 min."""
    from apscheduler.triggers.interval import IntervalTrigger

    from kubo.scheduler import Schedules, build_scheduler

    schedules = Schedules(timezone="America/Sao_Paulo", schedules=[])
    scheduler = build_scheduler(schedules, _scheduler_settings())

    poll_job = scheduler.get_job("digest_poll")
    assert poll_job is not None
    assert isinstance(poll_job.trigger, IntervalTrigger)
    assert poll_job.trigger.interval.total_seconds() == 5 * 60


def test_check_and_reschedule_digest_reschedules_when_cron_changes() -> None:
    """`_check_and_reschedule_digest` chama `reschedule_job` com novo CronTrigger quando o
    cron do settings mudou."""
    from zoneinfo import ZoneInfo

    from kubo.scheduler import _check_and_reschedule_digest

    scheduler = MagicMock()
    db = _fake_db()

    def _get_settings(_db: Any) -> Settings:
        return _scheduler_settings(cron="0 20 * * *")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("kubo.scheduler.settings_store.get_settings", _get_settings)
        new_cron = _check_and_reschedule_digest(
            db, scheduler, ZoneInfo("America/Sao_Paulo"), "30 9 * * *"
        )

    assert new_cron == "0 20 * * *"
    scheduler.reschedule_job.assert_called_once()
    args, kwargs = scheduler.reschedule_job.call_args
    assert args == ("digest",)
    assert "trigger" in kwargs


def test_check_and_reschedule_digest_keeps_current_on_same_cron() -> None:
    """Sem mudança de cron, `reschedule_job` NÃO é chamado."""
    from zoneinfo import ZoneInfo

    from kubo.scheduler import _check_and_reschedule_digest

    scheduler = MagicMock()
    db = _fake_db()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "kubo.scheduler.settings_store.get_settings",
            _get_settings := lambda _db: _scheduler_settings(),
        )
        new_cron = _check_and_reschedule_digest(
            db, scheduler, ZoneInfo("America/Sao_Paulo"), "30 9 * * *"
        )

    assert new_cron == "30 9 * * *"
    scheduler.reschedule_job.assert_not_called()


def test_check_and_reschedule_digest_keeps_current_on_invalid_cron() -> None:
    """Cron inválido no settings é logado como erro e NÃO reagenda — mantém o atual."""
    from zoneinfo import ZoneInfo

    from kubo.scheduler import _check_and_reschedule_digest

    scheduler = MagicMock()
    db = _fake_db()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "kubo.scheduler.settings_store.get_settings",
            lambda _db: _scheduler_settings(cron="not a cron"),
        )
        new_cron = _check_and_reschedule_digest(
            db, scheduler, ZoneInfo("America/Sao_Paulo"), "30 9 * * *"
        )

    assert new_cron == "30 9 * * *"
    scheduler.reschedule_job.assert_not_called()


def test_check_and_reschedule_digest_keeps_current_when_settings_missing() -> None:
    """Settings ausente devolve `last_cron` sem reagendar."""
    from zoneinfo import ZoneInfo

    from kubo.scheduler import _check_and_reschedule_digest

    scheduler = MagicMock()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("kubo.scheduler.settings_store.get_settings", lambda _db: None)
        new_cron = _check_and_reschedule_digest(
            object(), scheduler, ZoneInfo("America/Sao_Paulo"), "30 9 * * *"
        )

    assert new_cron == "30 9 * * *"
    scheduler.reschedule_job.assert_not_called()


def test_execute_job_reads_distribution_paused_for_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """No disparo do `digest`, `execute_job` lê `distribution_paused` de settings e loga."""
    from kubo import scheduler

    logged: list[dict[str, Any]] = []

    monkeypatch.setattr(scheduler, "run_worker", lambda db, worker, *, config, embedder: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda cfg: _DummyCtx())

    settings_read: list[bool] = []

    def _get_settings(_db: Any) -> Settings:
        settings_read.append(True)
        return _scheduler_settings(paused=True)  # type: ignore[call-arg]

    monkeypatch.setattr(scheduler.settings_store, "get_settings", _get_settings)

    def _info(event: str, **kwargs: Any) -> None:
        logged.append({"event": event, **kwargs})

    monkeypatch.setattr(scheduler._log, "info", _info)

    scheduler.execute_job("digest", {})

    assert settings_read
    assert any(
        e.get("event") == "digest_pause_read" and e.get("distribution_paused") is True
        for e in logged
    )


def test_execute_job_runs_paused_digest_as_empty_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Com `distribution_paused=true`, `execute_job` RODA o worker digest com a flag
    pausada; o worker fecha `ok` sem dispatch e sem avançar watermark."""
    from kubo import scheduler

    instantiated: list[str] = []

    def _fake_worker(worker_name: str) -> tuple[Any, None]:
        instantiated.append(worker_name)
        raise AssertionError("não deve instanciar o digest real quando pausado")

    calls: list[tuple[Any, dict[str, Any]]] = []

    def _fake_run_worker(_db: Any, worker: Any, *, config: dict[str, Any], embedder: Any) -> None:
        calls.append((worker, config))

    monkeypatch.setattr(scheduler, "_instantiate", _fake_worker)
    monkeypatch.setattr(scheduler, "run_worker", _fake_run_worker)
    monkeypatch.setattr(scheduler.client, "connect", lambda cfg: _DummyCtx())
    monkeypatch.setattr(
        scheduler.settings_store,
        "get_settings",
        lambda _db: _scheduler_settings(paused=True),
    )

    scheduler.execute_job("digest", {})

    assert not instantiated
    assert len(calls) == 1
    _worker, _config = calls[0]
    assert _worker.manifest.name == "digest"
    assert _config.get("paused") is True
    assert _config.get("max_items") == 50


def test_digest_pauses_when_settings_read_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha ao ler settings no disparo do digest deve ser fail-safe: digest pausado."""
    from kubo import scheduler

    calls: list[dict[str, Any]] = []

    def _fake_run_worker(_db: Any, worker: Any, *, config: dict[str, Any], embedder: Any) -> None:
        calls.append(config)

    monkeypatch.setattr(scheduler, "run_worker", _fake_run_worker)
    monkeypatch.setattr(scheduler.client, "connect", lambda cfg: _DummyCtx())
    monkeypatch.setattr(
        scheduler.settings_store,
        "get_settings",
        lambda _db: (_ for _ in ()).throw(Exception("db down")),
    )

    scheduler.execute_job("digest", {})

    assert calls == [{"max_items": 50, "paused": True}]
