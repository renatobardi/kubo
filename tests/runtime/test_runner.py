"""Runner de worker ponta a ponta (integração, SurrealDB).

Cobre plano 0004 §4.2: worker fake validado contra o contrato, executado com
ctx escopado, RunResult persistido pelo runtime via store e ciclo de `run`
registrado — nos caminhos SUCESSO e FALHA. O worker fake vive AQUI (tests/),
não em `kubo/workers/`, que é só para os portados reais (C6).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from pydantic import BaseModel

from kubo.contracts.models import ErrorInfo, ItemPayload, RunResult, SourcePayload, Stats
from kubo.contracts.worker import RunContext, WorkerManifest
from kubo.errors import ContractError, StoreError
from kubo.store import client, migrations

pytestmark = pytest.mark.integration

_RUNNER_DB = "test_runner"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema do zero."""
    cfg = replace(client.config(), database=_RUNNER_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_RUNNER_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_RUNNER_DB};")


def _count(db: Any, table: str) -> int:
    rows: list[dict[str, Any]] = db.query(f"SELECT count() FROM {table} GROUP ALL;")  # noqa: S608
    return int(rows[0]["count"]) if rows else 0


class _FeedConfig(BaseModel):
    """Schema de config do worker fake — o runtime valida e entrega instanciado."""

    feed_url: str


class _SuccessWorker:
    """Worker conforme: lê config validada, exige a integração declarada e
    devolve source+item + stats. Não toca a store — o runtime persiste."""

    manifest = WorkerManifest(
        name="fake-feed", version="0.1.0", integrations=["rss"], config=_FeedConfig
    )

    def run(self, ctx: RunContext) -> RunResult:
        assert isinstance(ctx.config, _FeedConfig)  # narrowing (ADR-0009 item II)
        assert "rss" in ctx.integrations  # prova a injeção de integração declarada
        ctx.logger.info("collected", items=1)  # só contadores, nunca payload
        source = SourcePayload(kind="rss", canonical=ctx.config.feed_url)
        item = ItemPayload(source=source, external_id="ep-1", content="conteúdo bruto")
        stats = Stats(items_seen=1, items_written=1)  # type: ignore[call-arg]  # extra="allow"
        return RunResult(payloads=[source, item], stats=stats)


class _RaisingWorker:
    """Worker que estoura no meio — a exceção é capturada na fronteira."""

    manifest = WorkerManifest(name="fake-boom", version="0.1.0", config=_FeedConfig)

    def run(self, ctx: RunContext) -> RunResult:
        raise ValueError("parse falhou no trecho coletado: " + "x" * 800)


class _SoftErrorWorker:
    """Worker que devolve payloads E error: o runtime persiste os itens e SÓ
    DEPOIS fecha o run em erro (ADR-0009 item VII)."""

    manifest = WorkerManifest(name="fake-soft", version="0.1.0", config=_FeedConfig)

    def run(self, ctx: RunContext) -> RunResult:
        source = SourcePayload(kind="rss", canonical="https://x/feed")
        return RunResult(
            payloads=[source], error=ErrorInfo(kind="partial", message="metade falhou")
        )


class _BadResultWorker:
    """Worker que devolve um resultado que não valida como RunResult."""

    manifest = WorkerManifest(name="fake-bad", version="0.1.0", config=_FeedConfig)

    def run(self, ctx: RunContext) -> RunResult:
        return {"payloads": [{"type": "item"}]}  # type: ignore[return-value]  # item incompleto


class _NoManifestWorker:
    """Não honra o contrato — sem manifest."""

    def run(self, ctx: RunContext) -> RunResult:
        return RunResult()


class _SecretExfilWorker:
    """Worker hostil que tenta exfiltrar o segredo resolvido pelo caminho de erro."""

    manifest = WorkerManifest(
        name="fake-exfil", version="0.1.0", integrations=["svc"], config=_FeedConfig
    )

    def run(self, ctx: RunContext) -> RunResult:
        raise RuntimeError(f"leak: {ctx.integrations['svc']}")


def test_run_worker_success_persists_and_finishes(db: Any) -> None:
    """Caminho feliz: worker validado executa, o runtime persiste source+item e
    fecha o run em 'ok' com os stats devolvidos."""
    from kubo.runtime.runner import run_worker

    run_id = run_worker(db, _SuccessWorker(), config={"feed_url": "https://x/feed"})

    row = db.query("SELECT status, stats FROM $r;", {"r": run_id})[0]
    assert row["status"] == "ok"
    assert row["stats"]["items_written"] == 1
    assert _count(db, "source") == 1
    assert _count(db, "item") == 1
    linked = db.query("SELECT ->from_source->source AS s FROM item;")[0]["s"]
    assert len(linked) == 1  # a aresta item->source foi criada pelo runtime
    collected = db.query("SELECT ->collected_by->run AS r FROM item;")[0]["r"]
    assert collected == [run_id]  # proveniência de execução item->run (ADR-0008 §VI)


def test_run_worker_failure_records_structured_error_and_persists_nothing(db: Any) -> None:
    """Caminho de falha: worker estoura → run fecha em 'error' com erro
    estruturado, message TRUNCADA (não vaza o trecho coletado) e NADA persistido."""
    from kubo.runtime.runner import run_worker

    run_id = run_worker(db, _RaisingWorker(), config={"feed_url": "https://x/feed"})

    row = db.query("SELECT status, error, finished_at FROM $r;", {"r": run_id})[0]
    assert row["status"] == "error"
    assert row["error"]["kind"] == "worker_exception"
    assert len(row["error"]["message"]) <= 500  # truncado (ADR-0009 item VIII)
    assert row["finished_at"] is not None
    assert _count(db, "source") == 0
    assert _count(db, "item") == 0


def test_run_worker_soft_error_persists_payloads_then_fails(db: Any) -> None:
    """RunResult com payloads E error: os payloads entregues são persistidos e
    o run fecha em 'error' (ADR-0009 item VII)."""
    from kubo.runtime.runner import run_worker

    run_id = run_worker(db, _SoftErrorWorker(), config={"feed_url": "https://x/feed"})

    row = db.query("SELECT status, error FROM $r;", {"r": run_id})[0]
    assert row["status"] == "error"
    assert row["error"]["kind"] == "partial"
    assert _count(db, "source") == 1  # o payload entregue foi persistido antes do fail


def test_run_worker_rejects_invalid_result(db: Any) -> None:
    """RunResult inválido (item incompleto) é rejeitado na validação → run em
    'error', sem persistir o payload malformado (regra 2 de D6)."""
    from kubo.runtime.runner import run_worker

    run_id = run_worker(db, _BadResultWorker(), config={"feed_url": "https://x/feed"})

    row = db.query("SELECT status FROM $r;", {"r": run_id})[0]
    assert row["status"] == "error"
    assert _count(db, "item") == 0


def test_run_worker_rejects_invalid_worker_before_opening_run(db: Any) -> None:
    """Worker que não honra o contrato levanta ContractError ANTES de abrir o
    run — worker inválido nunca executa, sem run órfão (ADR-0009 item V)."""
    from kubo.runtime.runner import run_worker

    before = _count(db, "run")
    with pytest.raises(ContractError):
        run_worker(db, _NoManifestWorker())

    assert _count(db, "run") == before


def test_run_worker_persist_failure_closes_run_as_error(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha na persistência não deixa o run travado em 'running' nem propaga
    exceção crua — o runner fecha o run em 'error' (a fronteira não explode,
    fecha o achado #3 da revisão: _persist estava fora do try)."""
    from kubo.runtime import runner

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise StoreError("store indisponível no meio da persistência")

    monkeypatch.setattr(runner, "upsert_source", _boom)

    run_id = runner.run_worker(db, _SuccessWorker(), config={"feed_url": "https://x/feed"})

    assert db.query("SELECT status FROM $r;", {"r": run_id})[0]["status"] == "error"


def test_run_worker_error_does_not_leak_secret(
    db: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Worker hostil que estoura com o objeto de integração na exceção NÃO
    exfiltra o segredo resolvido para run.error (repr do segredo é redigido)."""
    (tmp_path / "svc.yaml").write_text(
        "name: svc\nkind: http\nauth:\n  type: bearer\n  secret_ref: env:KUBO_LEAK_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KUBO_LEAK_TOKEN", "sk-do-not-leak")
    from kubo.runtime.runner import run_worker

    run_id = run_worker(
        db, _SecretExfilWorker(), config={"feed_url": "https://x/feed"}, catalog_dir=tmp_path
    )

    error = db.query("SELECT error FROM $r;", {"r": run_id})[0]["error"]
    assert error["kind"] == "worker_exception"
    assert "sk-do-not-leak" not in error["message"]
