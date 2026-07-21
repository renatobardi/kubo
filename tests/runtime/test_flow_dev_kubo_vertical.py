"""Vertical do flow `dev-kubo` (ADR-0021 §9, D41/E8) — o SEGUNDO alvo do `dev-mini`, mirado no
repo PRINCIPAL `renatobardi/kubo` em vez do sandbox `kubo-forge`.

Espelha `test_flow_dev_vertical.py` (mesmo idioma: store/runner reais, gitops/github_api/
`_build_cli_executor` monkeypatchados) mas prova que o SEGUNDO alvo é uma coisa DIFERENTE do
primeiro, não um alias: branch com prefixo `agent/` (não `kubo/`), integração de escrita
`github-kubo` (não `github` — token `_MAIN_PAT`, não `_PAT` do forge), e o tripwire de
pr_url-vs-target (E8 defesa-em-profundidade contra colisão de número de PR entre repos).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import StateError
from kubo.executors.cli import CliOutcome
from kubo.runtime import flow_runner
from kubo.runtime.flow_runner import promote_gate, reject_gate, resume_gate, run_flow
from kubo.store.destinations import Destination
from kubo.workers import github_api
from kubo.workers.github_api import PrRef, PrStatus
from tests.runtime.conftest import FakeCli, fake_gitops, promotion_gate

pytestmark = pytest.mark.integration

_DB = "test_flow_dev_kubo_vertical"
_DEST = Destination(
    id=RecordID("destination", "owner-telegram"),
    name="Renato",
    kind="pessoa",
    channel="telegram",
    address="chat-1",
    enabled=True,
    archived_at=None,
)
_BASE = "https://kubo.example"
_MAIN_PAT = "fake-kubo-pat-do-not-leak"
_RO_TOKEN = "fake-readonly-token-do-not-leak"
_OWNER = "renatobardi"
_REPO = "kubo"


@pytest.fixture(autouse=True)
def _kubo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env do repo PRINCIPAL (D41) + PAT dedicado (§8): sem isto o flow `dev-kubo` falha alto na
    montagem (ConfigError) — o mesmo fail-fast do `_FORGE_ENV`, mas para `_MAIN_ENV`."""
    monkeypatch.setenv("GITHUB_PAT_KUBO", _MAIN_PAT)
    monkeypatch.setenv("GITHUB_TOKEN_READONLY", _RO_TOKEN)
    monkeypatch.setenv("KUBO_MAIN_REPO_URL", "https://github.com/renatobardi/kubo.git")
    monkeypatch.setenv("KUBO_MAIN_OWNER", _OWNER)
    monkeypatch.setenv("KUBO_MAIN_REPO", _REPO)
    monkeypatch.setenv("KUBO_MAIN_GIT_NAME", "Kubo Dev")
    monkeypatch.setenv("KUBO_MAIN_GIT_EMAIL", "dev@kubo.local")


@pytest.fixture
def db(make_db: Callable[[str], Any]) -> Iterator[Any]:
    """Database próprio do teste (`make_db`, tests/runtime/conftest.py) — schema do zero."""
    yield make_db(_DB)


def _run_to_gate(
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: CliOutcome | None = None,
    pr_url: str = f"https://github.com/{_OWNER}/{_REPO}/pull/42",
    pr_number: int = 42,
) -> tuple[Any, dict[str, Any]]:
    """Instancia e roda o dev-kubo até o gate (ou até falhar). Devolve (result, open_pr_kwargs).

    `pr_url`/`pr_number` são injetáveis: os testes de tripwire simulam um deliverable que NÃO
    pertence ao alvo `dev-kubo` (ex.: uma PrRef devolvida sob `kubo-forge`) para provar que
    `reject_gate`/`promote_gate` barram ANTES de qualquer I/O contra a API."""
    fake_gitops(monkeypatch)
    out = outcome or CliOutcome(text="implementei feature X", cost_usd=0.37, num_turns=2)
    monkeypatch.setattr(flow_runner, "_build_cli_executor", lambda p, t: FakeCli(out))
    opened: dict[str, Any] = {}

    def _open(**kw: Any) -> PrRef:
        opened.update(kw)
        return PrRef(url=pr_url, number=pr_number)

    monkeypatch.setattr(github_api, "open_pull_request", _open)
    result = run_flow(db, template_name="dev-kubo", question="fix bug Y", base_url=_BASE)
    return result, opened


def test_run_parks_at_review_with_agent_branch_and_kubo_pat(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O SEGUNDO alvo não é um alias do primeiro: o PR abre de um branch `agent/*` (E3 identifica
    PRs de agente por prefixo, não `kubo/*` do forge) e usa o PAT dedicado `github-kubo`, não o
    `github` do sandbox."""
    result, opened = _run_to_gate(db, monkeypatch)

    assert result.state == "review"
    assert result.gate_task is not None
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "review"
    deliv = db.query(
        "SELECT kind, pr_url, pr_number FROM $f->produces->deliverable;", {"f": result.flow}
    )
    assert len(deliv) == 1
    assert deliv[0]["kind"] == "pr"
    assert deliv[0]["pr_url"] == f"https://github.com/{_OWNER}/{_REPO}/pull/42"
    assert opened["base"] == "main"
    assert opened["head"].startswith("agent/")
    assert not opened["head"].startswith("kubo/")  # não é o prefixo do forge
    assert opened["owner"] == _OWNER
    assert opened["repo"] == _REPO
    assert opened["token"] == _MAIN_PAT  # NÃO o PAT do forge


def test_reject_closes_pr_via_api_with_kubo_pat(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejeição do dev-kubo: fecha o PR via API com o PAT DEDICADO `github-kubo` — prova que
    `_reject_dev` resolve a integração do TARGET, não hardcoded no `github` do forge."""
    result, _ = _run_to_gate(db, monkeypatch)
    closed: dict[str, Any] = {}
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: closed.update(kw))

    reject_gate(db, gate_task=result.gate_task, reason="escopo errado")

    assert closed["number"] == 42
    assert closed["owner"] == _OWNER
    assert closed["repo"] == _REPO
    assert closed["token"] == _MAIN_PAT
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "rejected"


def test_promote_still_uses_shared_readonly_token(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmar promoção do dev-kubo lê o merge com o MESMO token READ-ONLY compartilhado do
    forge (E12/E13 — o PAT read-only já cobre os dois repos operacionalmente, não é preocupação
    de target): prova que a generalização por `target` NÃO regrediu o caminho de leitura."""
    result, _ = _run_to_gate(db, monkeypatch)
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: None)
    resume_gate(db, gate_task=result.gate_task, destination=_DEST, base_url=_BASE)
    promo = promotion_gate(db, result.flow)
    assert promo is not None
    seen: dict[str, Any] = {}

    def _get(**kw: Any) -> PrStatus:
        seen.update(kw)
        return PrStatus(merged=True, merge_commit_sha="cafef00d")

    monkeypatch.setattr(github_api, "get_pull_request", _get)

    promote_gate(db, gate_task=promo, worker_name="feed")

    assert seen["token"] == _RO_TOKEN  # NUNCA o PAT de escrita do dev-kubo
    assert seen["owner"] == _OWNER
    assert seen["repo"] == _REPO
    assert seen["number"] == 42
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "promoted"


def test_reject_tripwire_blocks_pr_url_mismatch_before_any_api_call(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E8 defesa-em-profundidade: se o deliverable guarda um `pr_url` que NÃO bate com
    owner/repo do alvo `dev-kubo` (ex.: uma PrRef simulando ter aberto sob `kubo-forge` — um
    bug hipotético de dispatch cruzado), `reject_gate` deve barrar com `StateError` ANTES de
    qualquer chamada à API do GitHub — nunca comentar/fechar o PR errado."""
    result, _ = _run_to_gate(
        db,
        monkeypatch,
        pr_url="https://github.com/owner/kubo-forge/pull/9",
        pr_number=9,
    )
    closed: dict[str, Any] = {}
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: closed.update(kw))

    with pytest.raises(StateError):
        reject_gate(db, gate_task=result.gate_task, reason="pr_url não bate com o alvo")

    assert closed == {}  # nenhuma chamada à API do GitHub


def test_promote_tripwire_blocks_pr_url_mismatch_before_any_api_call(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesmo tripwire (E8) no caminho de promoção: `promote_gate` deve barrar com `StateError`
    ANTES de consultar `get_pull_request` quando o `pr_url` armazenado não bate com o alvo."""
    result, _ = _run_to_gate(
        db,
        monkeypatch,
        pr_url="https://github.com/owner/kubo-forge/pull/9",
        pr_number=9,
    )
    monkeypatch.setattr(github_api, "close_pull_request", lambda **kw: None)
    resume_gate(db, gate_task=result.gate_task, destination=_DEST, base_url=_BASE)
    promo = promotion_gate(db, result.flow)
    assert promo is not None
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        github_api,
        "get_pull_request",
        lambda **kw: seen.update(kw) or PrStatus(merged=True, merge_commit_sha="deadbeef"),
    )

    with pytest.raises(StateError):
        promote_gate(db, gate_task=promo, worker_name="feed")

    assert seen == {}  # nenhuma chamada à API do GitHub (get_pull_request não foi invocado)
