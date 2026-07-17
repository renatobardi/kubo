"""Vertical do flow `pipeline` (integração, SurrealDB real — ADR-0021 §21.3/21.4).

Prova o encanamento inteiro com o worker `github-releases` REAL (só a rede é falsa, via
respx) mas store e runner REAIS: `run_flow(pipeline)` → instantiate_flow (snapshot) →
create_task(coletor, queued) → transição queued→collecting → run_worker(GithubReleasesWorker)
→ transição collecting→stored|failed segundo o run. Sem gate humano (C6, board só tem
`queued/collecting/stored/failed`) e sem executor/embedder/destination (a persona `coletor`
não usa LLM — `kubo/personas/coletor.yaml`).

`GITHUB_TOKEN_WATCH` é a integração dedicada (`catalogs/integrations/github-watch.yaml`)
que o worker resolve via runtime (nunca `os.environ` direto no worker) — o teste só
precisa do env presente para a resolução EAGER de integrações não falhar antes do worker
rodar (mesmo idioma do `_telegram_token` de `test_flow_vertical.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from kubo.runtime.flow_runner import run_flow
from kubo.store import client, migrations
from kubo.store.knowledge import run_status

pytestmark = pytest.mark.integration

_DB = "test_flow_pipeline_vertical"
_GITHUB = "https://api.github.com"
_SINCE = datetime(2026, 7, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _watch_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolução EAGER de integrações (`_build_context`) exige o token no env — sem isto
    o run falharia (ConfigError) antes mesmo de `GithubReleasesWorker.run` tentar a rede."""
    monkeypatch.setenv("GITHUB_TOKEN_WATCH", "fake-watch-token")  # pragma: allowlist secret


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _graphql_watching_response(*full_names: str) -> dict[str, Any]:
    """Corpo de `viewer.watching` (D57 — descoberta migrou de REST pra GraphQL), uma
    página só (sem `hasNextPage`)."""
    return {
        "data": {
            "viewer": {
                "watching": {
                    "nodes": [{"nameWithOwner": name} for name in full_names],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }


def _release(release_id: int, *, published_at: str) -> dict[str, Any]:
    return {
        "id": release_id,
        "tag_name": "v1.0.0",
        "name": "Release One",
        "body": "Corpo **um**.",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/acme/widget/releases/tag/v1.0.0",
        "published_at": published_at,
    }


@respx.mock
def test_pipeline_collects_release_and_lands_in_stored(db: Any) -> None:
    """Caminho feliz: um repo assistido com uma release publicada após `since` → o run
    fecha `ok`, o task termina em `stored` (sem gate) e o run fica linkado (`set_task_run`)."""
    respx.post(f"{_GITHUB}/graphql").mock(
        return_value=httpx.Response(200, json=_graphql_watching_response("acme/widget"))
    )
    respx.get(f"{_GITHUB}/repos/acme/widget/releases").mock(
        return_value=httpx.Response(200, json=[_release(42, published_at="2026-07-10T00:00:00Z")])
    )

    result = run_flow(
        db,
        template_name="pipeline",
        question="coleta diária",
        worker_config={"since": _SINCE},  # type: ignore[call-arg]
        base_url="",
    )

    assert result.state == "stored"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "stored"
    assert run_status(db, result.run) == "ok"
    assert db.query("SELECT VALUE run FROM $t;", {"t": result.task})[0] == result.run
    flow = db.query("SELECT template_name, snapshot FROM $f;", {"f": result.flow})[0]
    assert flow["template_name"] == "pipeline"
    assert flow["snapshot"]["board"]["states"] == ["queued", "collecting", "stored", "failed"]


@respx.mock
def test_pipeline_subscriptions_failure_lands_in_failed(db: Any) -> None:
    """A busca da watch list falha (500) — não "aprendi que não há watches", mas "não
    consegui nem perguntar" (D55): o run fecha em erro e o task termina em `failed`."""
    respx.post(f"{_GITHUB}/graphql").mock(return_value=httpx.Response(500))

    result = run_flow(
        db,
        template_name="pipeline",
        question="coleta diária",
        worker_config={"since": _SINCE},  # type: ignore[call-arg]
        base_url="",
    )

    assert result.state == "failed"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "failed"
    assert run_status(db, result.run) == "error"


@respx.mock
def test_pipeline_empty_watch_list_is_config_error_and_still_lands_in_failed(db: Any) -> None:
    """Watch list vazia é ERRO, nunca run limpo (C3 CRITICAL): `error.kind == "config"` no
    run, e o task ainda pousa em `failed` — pipeline v1 só tem `stored|failed`, sem gate
    pra rotear um erro de config pra um terceiro estado."""
    respx.post(f"{_GITHUB}/graphql").mock(
        return_value=httpx.Response(200, json=_graphql_watching_response())
    )

    result = run_flow(
        db,
        template_name="pipeline",
        question="coleta diária",
        worker_config={"since": _SINCE},  # type: ignore[call-arg]
        base_url="",
    )

    assert result.state == "failed"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "failed"
    assert db.query("SELECT VALUE error.kind FROM $r;", {"r": result.run})[0] == "config"


def test_pipeline_bad_worker_config_does_not_crash_and_lands_in_failed(db: Any) -> None:
    """`worker_config={}` falta o campo obrigatório `since` de `GithubReleasesConfig`. Essa
    validação roda DENTRO de `run_worker` (`_build_context` chama
    `manifest.config.model_validate`), cuja fronteira já captura `ValidationError` e fecha o
    run em erro estruturado (`kind="contract"`, `kubo/runtime/runner.py:_error_from_exception`)
    em vez de deixar a exceção propagar — é o contrato JÁ GARANTIDO por `run_worker` para
    qualquer worker com config malformada (ver `tests/scheduler/test_scheduler.py`, config
    inválida vira `ConfigError` só no BUILD do scheduler, nunca no disparo).

    Este teste é de REGRESSÃO DE SHAPE, não uma decisão de design nova: `run_flow` não deve
    crashar, e o task deve pousar em `failed` (pipeline v1 não tem rota alternativa pra erro
    de config malformado, só `stored|failed` — sem gate pra desviar). Nenhuma chamada de rede
    ocorre: a validação de config falha ANTES do worker tentar `viewer.watching` (D57)."""
    result = run_flow(
        db,
        template_name="pipeline",
        question="coleta diária",
        worker_config={},  # type: ignore[call-arg]
        base_url="",
    )

    assert result.state == "failed"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "failed"
    assert run_status(db, result.run) == "error"
