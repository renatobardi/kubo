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

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from kubo.distribution.destinations import ResolvedDestination
from kubo.errors import PromotionError, StateError
from kubo.executors.cli import CliOutcome
from kubo.runtime import flow_runner
from kubo.runtime.flow_runner import promote_gate, reject_gate, resume_gate, run_flow
from kubo.store.flows import read_gate_context
from kubo.workers import github_api
from kubo.workers.github_api import PrRef, PrStatus
from tests.runtime.conftest import FakeCli, fake_gitops, promotion_gate

pytestmark = pytest.mark.integration

_DB = "test_flow_dev_vertical"
_DEST = ResolvedDestination(
    id="owner-telegram", name="Renato", kind="pessoa", channel="telegram", address="chat-1"
)
_BASE = "https://kubo.example"
_PAT = "fake-forge-pat-do-not-leak"
_RO_TOKEN = "fake-readonly-token-do-not-leak"


@pytest.fixture(autouse=True)
def _forge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env do sandbox (D37) + PAT (§8): o worker/behavior resolve tudo daqui, o git/GitHub são
    falsos e ignoram os valores. Sem isto o flow dev falha alto na montagem (ConfigError)."""
    monkeypatch.setenv("GITHUB_PAT_FORGE", _PAT)
    monkeypatch.setenv("GITHUB_TOKEN_READONLY", _RO_TOKEN)
    monkeypatch.setenv("KUBO_FORGE_REPO_URL", "https://github.com/owner/kubo-forge.git")
    monkeypatch.setenv("KUBO_FORGE_OWNER", "owner")
    monkeypatch.setenv("KUBO_FORGE_REPO", "kubo-forge")
    monkeypatch.setenv("KUBO_FORGE_GIT_NAME", "Kubo Dev")
    monkeypatch.setenv("KUBO_FORGE_GIT_EMAIL", "dev@kubo.local")


@pytest.fixture
def db(make_db: Callable[[str], Any]) -> Iterator[Any]:
    """Database próprio do teste (`make_db`, tests/runtime/conftest.py) — schema do zero."""
    yield make_db(_DB)


def _run_to_gate(
    db: Any, monkeypatch: pytest.MonkeyPatch, *, outcome: CliOutcome | None = None
) -> tuple[Any, dict[str, Any]]:
    """Instancia e roda o dev-mini até o gate (ou até falhar). Devolve (result, open_pr_kwargs)."""
    fake_gitops(monkeypatch)
    out = outcome or CliOutcome(text="implementei hello()", cost_usd=0.42, num_turns=3)
    monkeypatch.setattr(flow_runner, "_build_cli_executor", lambda p, t: FakeCli(out))
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


def test_approve_auto_opens_promotion_gate_v2(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 (ADR-0021): aprovar o gate `review` abre AUTOMATICAMENTE o gate de promoção — nova task
    humana em `done`, sem decisão, resolvível como gate aberto (`gate_state='done'`)."""
    result, _ = _run_to_gate(db, monkeypatch)
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: None)

    resume_gate(db, gate_task=result.gate_task, destination=_DEST, base_url=_BASE)

    promo = promotion_gate(db, result.flow)
    assert promo is not None
    ctx = read_gate_context(db, promo)
    assert ctx is not None
    assert ctx.gate_state == "done"
    assert ctx.counterpart_task == result.task  # a dev, única não-humana


def test_reject_on_promotion_gate_leaves_merged_pr_untouched(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trap c (ADR-0021 §9): rejeitar o gate de PROMOÇÃO (par [done, rejected] inexistente) falha
    ANTES de qualquer I/O — o PR JÁ MESCLADO não é comentado nem fechado."""
    result, _ = _run_to_gate(db, monkeypatch)
    closed: dict[str, Any] = {}
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: closed.update(kw))
    resume_gate(db, gate_task=result.gate_task, destination=_DEST, base_url=_BASE)
    promo = promotion_gate(db, result.flow)

    with pytest.raises(StateError):
        reject_gate(db, gate_task=promo, reason="não quero promover")

    assert closed == {}  # nenhuma chamada à API do GitHub


def _approved_to_promotion(db: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """Roda o dev-mini até o gate de promoção ABERTO (review aprovado). Devolve (result, promo)."""
    result, _ = _run_to_gate(db, monkeypatch)
    resume_gate(db, gate_task=result.gate_task, destination=_DEST, base_url=_BASE)
    promo = promotion_gate(db, result.flow)
    return result, promo


def test_promote_confirms_merged_pr_and_registered_worker(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E10/E12: `promote_gate` lê o merge com o token READ-ONLY (nunca o PAT de escrita),
    valida `worker_name` no `WORKER_REGISTRY` real (`feed`, sempre presente), grava o
    `merge_commit_sha` no deliverable e move as 2 tasks a `promoted` (terminal v2)."""
    result, promo = _approved_to_promotion(db, monkeypatch)
    seen: dict[str, Any] = {}

    def _get(**kw: Any) -> PrStatus:
        seen.update(kw)
        return PrStatus(merged=True, merge_commit_sha="deadbeef123")

    monkeypatch.setattr(github_api, "get_pull_request", _get)

    promote_gate(db, gate_task=promo, worker_name="feed")

    assert seen["token"] == _RO_TOKEN  # NUNCA o PAT de escrita
    assert seen["number"] == 9
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "promoted"
    gate = db.query("SELECT state, decision FROM $t;", {"t": promo})[0]
    assert gate == {"state": "promoted", "decision": "approved"}
    deliv = db.query(
        "SELECT VALUE merge_commit_sha FROM $f->produces->deliverable;", {"f": result.flow}
    )
    assert deliv == ["deadbeef123"]


def test_promote_rejects_when_pr_not_merged(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aprovar no board ≠ mesclar no GitHub (D38): se a API diz `merged:false`, o Confirmar
    falha com `PromotionError` e o gate SEGUE ABERTO (o dono relê e reclica)."""
    _result, promo = _approved_to_promotion(db, monkeypatch)
    monkeypatch.setattr(
        github_api, "get_pull_request", lambda **kw: PrStatus(merged=False, merge_commit_sha=None)
    )

    with pytest.raises(PromotionError):
        promote_gate(db, gate_task=promo, worker_name="feed")

    assert db.query("SELECT VALUE state FROM $t;", {"t": promo})[0] == "done"
    assert read_gate_context(db, promo) is not None  # ainda um gate aberto


def test_promote_rejects_unknown_worker_name(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Import-oráculo (E10/E14): merge confirmado mas `worker_name` NÃO resolve no registry do
    processo vivo (deploy não rodou) → `PromotionError`, gate segue aberto."""
    _result, promo = _approved_to_promotion(db, monkeypatch)
    monkeypatch.setattr(
        github_api,
        "get_pull_request",
        lambda **kw: PrStatus(merged=True, merge_commit_sha="sha123"),
    )

    with pytest.raises(PromotionError):
        promote_gate(db, gate_task=promo, worker_name="does-not-exist")

    assert db.query("SELECT VALUE state FROM $t;", {"t": promo})[0] == "done"
    assert read_gate_context(db, promo) is not None  # ainda um gate aberto


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
