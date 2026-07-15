"""Testes do `DevWorker` (ADR-0019) — orquestra clone→agente→diff-check→push→PR.

Worker de CONTRATO PLENO (manifest + run(ctx)->RunResult): o PR volta como `PrPayload` no
`RunResult`, persistido pelo MESMO caminho do contrato (shape A). Aqui as primitivas já
seguras (gitops C2, github_api E3, CliExecutor) são MOCKADAS — o foco é a orquestração:
sequência correta, diff-check (E5), erro estruturado, limpeza do workspace (E7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from kubo.contracts.models import ErrorInfo, PrPayload, RunResult
from kubo.errors import ContractError, ForgeError
from kubo.executors.cli import CliOutcome
from kubo.runtime.integrations import ResolvedIntegration
from kubo.workers import dev, github_api, gitops
from kubo.workers.dev import DevConfig, DevWorker
from kubo.workers.github_api import PrRef

_PAT = "fake-forge-pat-do-not-leak"
_REPO_URL = "https://github.com/owner/kubo-forge.git"


class _FakeCli:
    """CliRunner fake: devolve um CliOutcome preset e grava prompt/workspace."""

    def __init__(self, outcome: CliOutcome) -> None:
        self._outcome = outcome
        self.prompt: str | None = None
        self.workspace: str | None = None

    def run(self, prompt: str, *, workspace: str) -> CliOutcome:
        self.prompt = prompt
        self.workspace = workspace
        return self._outcome


class _StubLogger:
    def bind(self, **_: Any) -> _StubLogger:
        return self

    def info(self, *_: Any, **__: Any) -> None: ...
    def warning(self, *_: Any, **__: Any) -> None: ...


@dataclass
class _Ctx:
    config: Any
    integrations: dict[str, ResolvedIntegration]
    logger: Any = field(default_factory=_StubLogger)
    knowledge: Any = None
    embedder: Any = None


def _config(**over: Any) -> DevConfig:
    base: dict[str, Any] = {
        "instruction": "add a hello() function to hello.py",
        "repo_url": _REPO_URL,
        "owner": "owner",
        "repo": "kubo-forge",
        "branch": "kubo/flow-abc",
        "git_name": "Kubo Dev",
        "git_email": "dev@kubo.local",
    }
    base.update(over)
    return DevConfig(**base)


def _ctx(config: DevConfig | None = None) -> _Ctx:
    github = ResolvedIntegration(
        name="github",
        kind="http",
        auth_type="bearer",
        secret=_PAT,
        rate_limit=None,
        base_url="https://api.github.com",
    )
    return _Ctx(config=config or _config(), integrations={"github": github})


def _outcome(**over: Any) -> CliOutcome:
    base: dict[str, Any] = {
        "text": "implemented hello()",
        "cost_usd": 0.42,
        "num_turns": 3,
        "stop_reason": "end_turn",
        "error": None,
    }
    base.update(over)
    return CliOutcome(**base)


@pytest.fixture
def happy_gitops(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mocka gitops para o caminho feliz; grava as chamadas para asserção."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(gitops, "clone", lambda url, ws: calls.update(clone=(url, ws)))
    monkeypatch.setattr(
        gitops, "configure_identity", lambda ws, **kw: calls.update(identity=(ws, kw))
    )
    monkeypatch.setattr(gitops, "head_sha", lambda ws: "base-sha-000")
    monkeypatch.setattr(gitops, "create_branch", lambda ws, br: calls.update(branch=br))
    monkeypatch.setattr(gitops, "has_new_commits", lambda ws, base: True)
    monkeypatch.setattr(
        gitops, "push", lambda ws, br, *, repo_url, token: calls.update(push=(br, repo_url, token))
    )
    return calls


def test_success_opens_pr_and_returns_pr_payload(
    happy_gitops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: dict[str, Any] = {}

    def _open(**kw: Any) -> PrRef:
        opened.update(kw)
        return PrRef(url="https://github.com/owner/kubo-forge/pull/9", number=9)

    monkeypatch.setattr(github_api, "open_pull_request", _open)
    cli = _FakeCli(_outcome())

    result = DevWorker(cli, prompt="engenheiro disciplinado").run(_ctx())

    assert isinstance(result, RunResult)
    assert result.error is None
    assert len(result.payloads) == 1
    pr = result.payloads[0]
    assert isinstance(pr, PrPayload)
    assert pr.url == "https://github.com/owner/kubo-forge/pull/9"  # E3: da API
    assert pr.number == 9
    assert pr.summary == "implemented hello()"
    assert result.stats.model_dump().get("cost_usd") == pytest.approx(0.42)
    # C2: clone com URL sem credencial; PAT só no push
    assert happy_gitops["clone"][0] == _REPO_URL
    assert happy_gitops["push"][2] == _PAT
    # E3: o PR abre de head=branch → base, com o PAT (do ctx, não do env)
    assert opened["head"] == "kubo/flow-abc"
    assert opened["base"] == "main"
    assert opened["token"] == _PAT


def test_executor_error_skips_push_and_pr(
    happy_gitops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(github_api, "open_pull_request", lambda **kw: called.setdefault("pr", kw))
    cli = _FakeCli(_outcome(error=ErrorInfo(kind="budget", message="estourou")))

    result = DevWorker(cli, prompt="p").run(_ctx())

    assert result.error is not None
    assert result.error.kind == "budget"
    assert not result.payloads
    assert "push" not in happy_gitops  # não empurrou
    assert "pr" not in called  # não abriu PR


def test_empty_diff_fails_without_pr(
    happy_gitops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gitops, "has_new_commits", lambda ws, base: False)  # E5: nada a pushar
    called: dict[str, Any] = {}
    monkeypatch.setattr(github_api, "open_pull_request", lambda **kw: called.setdefault("pr", kw))
    cli = _FakeCli(_outcome())

    result = DevWorker(cli, prompt="p").run(_ctx())

    assert result.error is not None
    assert result.error.kind == "empty"
    assert not result.payloads
    assert "push" not in happy_gitops
    assert "pr" not in called


def test_forge_error_becomes_structured_error(
    happy_gitops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(ws: str, br: str, *, repo_url: str, token: str) -> None:
        raise ForgeError("git push falhou (rc=128)")

    monkeypatch.setattr(gitops, "push", _boom)
    result = DevWorker(_FakeCli(_outcome()), prompt="p").run(_ctx())

    assert result.error is not None
    assert result.error.kind == "forge"
    assert not result.payloads


def test_workspace_is_removed_after_run(
    happy_gitops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(gitops, "clone", lambda url, ws: seen.update(ws=ws))
    monkeypatch.setattr(
        github_api,
        "open_pull_request",
        lambda **kw: PrRef(url="https://github.com/o/r/pull/1", number=1),
    )
    DevWorker(_FakeCli(_outcome()), prompt="p").run(_ctx())

    # E7: o workspace efêmero é removido no finally (senão o disco do LXC enche)
    assert seen["ws"] and not os.path.exists(seen["ws"])


def test_wrong_config_type_raises_contract_error() -> None:
    class _Other(DevConfig):
        pass

    ctx = _Ctx(config="not a config", integrations={})  # type: ignore[arg-type]
    with pytest.raises(ContractError):
        DevWorker(_FakeCli(_outcome()), prompt="p").run(ctx)


def test_manifest_declares_github_integration() -> None:
    assert dev.DevWorker.manifest.integrations == ["github"]
    assert dev.DevWorker.manifest.config is DevConfig


def test_config_rejects_option_injection_in_repo_url_and_branch() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):  # repo_url que viraria flag do git
        _config(repo_url="--upload-pack=/evil")
    with pytest.raises(ValidationError):  # branch começando com '-'
        _config(branch="-x")
