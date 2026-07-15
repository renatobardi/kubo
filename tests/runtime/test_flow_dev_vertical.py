"""Vertical do flow `dev-mini` com GATE de PR (integração, SurrealDB real — ADR-0019 §VII/§IX).

Prova o ciclo com o CliExecutor + git + GitHub FALSOS mas store e runner REAIS: `run_flow(dev-mini)`
instancia → a dev "implementa" (CliOutcome fake) → o PR "abre" (PrRef fake, url/number da API) →
o flow PARA no gate `review` com deliverable `kind=pr` → `resume_gate` leva as 2 tasks a `done`
SEM merge (D38) → `reject_gate` FECHA o PR via API com o motivo e arquiva (rejected). E prova o
caminho de falha (agente com erro → `failed`, sem gate, sem PR).

O executor cli é injetado monkeypatchando `_build_cli_executor` (o teste do behavior não sobe o
SDK); gitops/github_api são monkeypatchados no módulo do worker (mesmo idioma do test_dev).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.distribution.destinations import ResolvedDestination
from kubo.executors.cli import CliOutcome
from kubo.runtime import flow_runner
from kubo.runtime.flow_runner import reject_gate, resume_gate, run_flow
from kubo.store import client, migrations
from kubo.workers import github_api, gitops
from kubo.workers.github_api import PrRef

pytestmark = pytest.mark.integration

_DB = "test_flow_dev_vertical"
_DEST = ResolvedDestination(
    id="owner-telegram", name="Renato", kind="pessoa", channel="telegram", address="chat-1"
)
_BASE = "https://kubo.example"
_PAT = "fake-forge-pat-do-not-leak"


@pytest.fixture(autouse=True)
def _forge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env do sandbox (D37) + PAT (§8): o worker/behavior resolve tudo daqui, o git/GitHub são
    falsos e ignoram os valores. Sem isto o flow dev falha alto na montagem (ConfigError)."""
    monkeypatch.setenv("GITHUB_PAT_FORGE", _PAT)
    monkeypatch.setenv("KUBO_FORGE_REPO_URL", "https://github.com/owner/kubo-forge.git")
    monkeypatch.setenv("KUBO_FORGE_OWNER", "owner")
    monkeypatch.setenv("KUBO_FORGE_REPO", "kubo-forge")
    monkeypatch.setenv("KUBO_FORGE_GIT_NAME", "Kubo Dev")
    monkeypatch.setenv("KUBO_FORGE_GIT_EMAIL", "dev@kubo.local")


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


class _FakeCli:
    """CliRunner fake: devolve um CliOutcome preset (a prosa untrusted do agente)."""

    def __init__(self, outcome: CliOutcome) -> None:
        self._outcome = outcome

    def run(self, prompt: str, *, workspace: str) -> CliOutcome:
        return self._outcome


def _fake_gitops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutraliza o git real: clone/identity/branch/push viram no-ops; o HEAD "avança" (E5)."""
    monkeypatch.setattr(gitops, "clone", lambda url, ws: None)
    monkeypatch.setattr(gitops, "configure_identity", lambda ws, **kw: None)
    monkeypatch.setattr(gitops, "head_sha", lambda ws: "base-sha-000")
    monkeypatch.setattr(gitops, "create_branch", lambda ws, br: None)
    monkeypatch.setattr(gitops, "has_new_commits", lambda ws, base: True)
    monkeypatch.setattr(gitops, "push", lambda ws, br, *, repo_url, token: None)


def _run_to_gate(
    db: Any, monkeypatch: pytest.MonkeyPatch, *, outcome: CliOutcome | None = None
) -> tuple[Any, dict[str, Any]]:
    """Instancia e roda o dev-mini até o gate (ou até falhar). Devolve (result, open_pr_kwargs)."""
    _fake_gitops(monkeypatch)
    out = outcome or CliOutcome(text="implementei hello()", cost_usd=0.42, num_turns=3)
    monkeypatch.setattr(flow_runner, "_build_cli_executor", lambda p, t: _FakeCli(out))
    opened: dict[str, Any] = {}

    def _open(**kw: Any) -> PrRef:
        opened.update(kw)
        return PrRef(url="https://github.com/owner/kubo-forge/pull/9", number=9)

    monkeypatch.setattr(github_api, "open_pull_request", _open)
    result = run_flow(db, template_name="dev-mini", question="add hello()", base_url=_BASE)
    return result, opened


def test_run_parks_at_review_with_pr_deliverable(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run-até-gate: a dev PARA em `review`, o humano recebe task em `review`, e o deliverable
    `kind=pr` guarda a URL/número ESTRUTURAIS da API (E3) + a prosa untrusted (E4). O PR abriu de
    head=branch(derivado do flow) → main, com o PAT do ctx."""
    result, opened = _run_to_gate(db, monkeypatch)

    assert result.state == "review"
    assert result.gate_task is not None
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "review"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.gate_task})[0] == "review"
    persona = db.query(
        "SELECT VALUE ->assigned_to->persona.catalog_name FROM $t;", {"t": result.gate_task}
    )[0]
    assert persona == ["humano"]
    deliv = db.query(
        "SELECT kind, content, pr_url, pr_number FROM $f->produces->deliverable;",
        {"f": result.flow},
    )
    assert len(deliv) == 1
    assert deliv[0]["kind"] == "pr"
    assert deliv[0]["content"] == "implementei hello()"
    assert deliv[0]["pr_url"] == "https://github.com/owner/kubo-forge/pull/9"
    assert deliv[0]["pr_number"] == 9
    assert opened["base"] == "main"
    assert opened["head"].startswith("kubo/")  # branch derivado do flow id (E5)
    assert opened["token"] == _PAT


def test_approve_closes_flow_done_without_merge(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aprovação (D38): as 2 tasks vão a `done`, NENHUMA chamada de merge/close ao GitHub — o Kubo
    não mergeia (o dono clica no GitHub). Só a decisão no grafo."""
    result, _ = _run_to_gate(db, monkeypatch)
    closed: dict[str, Any] = {}
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: closed.update(kw))

    resume_gate(db, gate_task=result.gate_task, destination=_DEST, base_url=_BASE)

    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "done"
    gate = db.query("SELECT state, decision FROM $t;", {"t": result.gate_task})[0]
    assert gate["state"] == "done"
    assert gate["decision"] == "approved"
    assert closed == {}  # nada foi fechado/mergeado


def test_reject_closes_pr_via_api_and_archives(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejeição (D38): FECHA o PR via API com o motivo (número ESTRUTURAL do deliverable) e leva
    as 2 tasks a `rejected`. Prova a ordem: o close ocorre com o pr_number + reason corretos."""
    result, _ = _run_to_gate(db, monkeypatch)
    closed: dict[str, Any] = {}
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: closed.update(kw))

    reject_gate(db, gate_task=result.gate_task, reason="escopo errado")

    assert closed["number"] == 9  # do campo estrutural, não do content
    assert closed["reason"] == "escopo errado"
    assert closed["owner"] == "owner"
    assert closed["token"] == _PAT
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "rejected"
    gate = db.query("SELECT state, decision, reason FROM $t;", {"t": result.gate_task})[0]
    assert gate["state"] == "rejected"
    assert gate["reason"] == "escopo errado"


def test_agent_failure_fails_flow_without_gate_or_pr(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agente com erro (budget/timeout/agent — E2) → task `failed`, SEM gate, SEM PR (E5). O flow
    não abre gate sobre trabalho que não existe."""
    from kubo.contracts.models import ErrorInfo

    bad = CliOutcome(
        text="", cost_usd=0.0, num_turns=0, error=ErrorInfo(kind="budget", message="estourou")
    )
    result, _ = _run_to_gate(db, monkeypatch, outcome=bad)

    assert result.state == "failed"
    assert result.gate_task is None
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "failed"
    assert db.query("SELECT VALUE ->produces->deliverable FROM $f;", {"f": result.flow})[0] == []
