"""Testes do `CliExecutor` (ADR-0019 §I/§IV/§V) — SDK mockado por um `query_fn` fake.

O seam do executor `cli` é SEPARADO do `Executor.complete` (single-shot, api): contrato
"prompt in → stream de eventos out" (agêntico). Os fakes injetam um async-generator no
lugar de `claude_agent_sdk.query`, então nenhum teste spawna o subprocess real nem toca a
rede — mesma disciplina do `test_api` (litellm mockado).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, CLINotFoundError, ResultMessage, TextBlock

from kubo.errors import ExecutorError
from kubo.executors.cli import CliExecutor, CliExecutorConfig, CliOutcome


def _result(**over: Any) -> ResultMessage:
    """ResultMessage mínimo do SDK com defaults sensatos; `over` cobre o que o teste varia."""
    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 2,
        "session_id": "sess-1",
    }
    base.update(over)
    return ResultMessage(**base)


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="claude-sonnet")


def _fake_query(
    messages: list[Any],
    *,
    capture: dict[str, Any] | None = None,
    delay: float = 0.0,
    raises: BaseException | None = None,
) -> Any:
    """Fábrica de um `query_fn` fake: async-gen que emite `messages` (opcionalmente após
    `delay`, ou levantando `raises`), e captura env/options/prompt no `capture`."""

    async def _q(*, prompt: str, options: Any, transport: Any = None) -> Any:
        if capture is not None:
            capture["env"] = dict(os.environ)
            capture["options"] = options
            capture["prompt"] = prompt
        if raises is not None:
            raise raises
        if delay:
            await asyncio.sleep(delay)
        for message in messages:
            yield message

    return _q


def _cfg(**over: Any) -> CliExecutorConfig:
    base: dict[str, Any] = {
        "model": "claude-sonnet-4-5",
        "budget_usd": 5.0,
        "max_turns": 10,
        "timeout_s": 30.0,
    }
    base.update(over)
    return CliExecutorConfig(**base)


def test_success_collects_prose_and_cost() -> None:
    query = _fake_query(
        [
            _assistant("done implementing"),
            _result(total_cost_usd=0.42, stop_reason="end_turn", num_turns=3),
        ]
    )
    out = CliExecutor(_cfg(), query_fn=query).run("do X", workspace="/w")
    assert isinstance(out, CliOutcome)
    assert out.error is None
    assert out.text == "done implementing"
    assert out.cost_usd == pytest.approx(0.42)
    assert out.num_turns == 3
    assert out.stop_reason == "end_turn"


def test_budget_overshoot_returns_budget_error() -> None:
    query = _fake_query([_assistant("spent a lot"), _result(total_cost_usd=6.5)])
    out = CliExecutor(_cfg(budget_usd=5.0), query_fn=query).run("x", workspace="/w")
    assert out.error is not None
    assert out.error.kind == "budget"
    assert out.cost_usd == pytest.approx(6.5)  # custo real ainda é reportado


def test_agent_error_returns_agent_error() -> None:
    query = _fake_query([_result(is_error=True, total_cost_usd=0.1)])
    out = CliExecutor(_cfg(), query_fn=query).run("x", workspace="/w")
    assert out.error is not None
    assert out.error.kind == "agent"


def test_missing_result_message_is_agent_error() -> None:
    query = _fake_query([_assistant("stream ended without a ResultMessage")])
    out = CliExecutor(_cfg(), query_fn=query).run("x", workspace="/w")
    assert out.error is not None
    assert out.error.kind == "agent"


def test_timeout_returns_timeout_error() -> None:
    query = _fake_query([_assistant("slow")], delay=0.5)
    out = CliExecutor(_cfg(timeout_s=0.05), query_fn=query).run("x", workspace="/w")
    assert out.error is not None
    assert out.error.kind == "timeout"


def test_env_whitelist_scrubs_parent_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("SURREAL_PASS", "top-secret-canary")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-secret")
    capture: dict[str, Any] = {}
    query = _fake_query([_assistant("ok"), _result(total_cost_usd=0.1)], capture=capture)

    CliExecutor(_cfg(), query_fn=query).run("x", workspace="/work/space")

    child_env = capture["env"]
    # subtração: segredos do pai somem do filho (o scrub, não options.env, faz isto)
    assert "SURREAL_PASS" not in child_env
    assert "TELEGRAM_BOT_TOKEN" not in child_env
    # presença: a key sobrevive — asserção nas DUAS direções (só ausência passaria se o
    # spawn quebrasse inteiro; advisor condição 1)
    assert child_env.get("ANTHROPIC_API_KEY") == "sk-test-123"
    # HOME aponta pro workspace (papel do override de options.env / E1)
    assert child_env.get("HOME") == "/work/space"
    # restore integral: o env do PAI volta intacto após o run (finally)
    assert os.environ.get("SURREAL_PASS") == "top-secret-canary"


def test_options_carry_whitelist_and_backstops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("SURREAL_PASS", "secret")
    capture: dict[str, Any] = {}
    query = _fake_query([_assistant("ok"), _result(total_cost_usd=0.1)], capture=capture)

    CliExecutor(_cfg(model="claude-sonnet-4-5", budget_usd=7.0, max_turns=15), query_fn=query).run(
        "x", workspace="/ws"
    )

    options = capture["options"]
    assert options.cwd == "/ws"
    assert options.model == "claude-sonnet-4-5"
    assert options.max_turns == 15
    assert options.max_budget_usd == pytest.approx(7.0)  # teto nativo do SDK (camada extra)
    assert "WebFetch" in options.disallowed_tools
    assert "WebSearch" in options.disallowed_tools
    assert options.permission_mode == "bypassPermissions"
    # options.env = MESMA whitelist (2ª camada, Q2): sem segredos, HOME no workspace
    assert "SURREAL_PASS" not in options.env
    assert options.env.get("ANTHROPIC_API_KEY") == "sk-test-123"
    assert options.env.get("HOME") == "/ws"


def test_cli_not_found_raises_executor_error() -> None:
    query = _fake_query([], raises=CLINotFoundError("no cli"))
    with pytest.raises(ExecutorError):
        CliExecutor(_cfg(), query_fn=query).run("x", workspace="/w")


def test_executor_error_never_leaks_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _fake_query([], raises=CLINotFoundError("secret path /home/kubo/.env leaked here"))
    with pytest.raises(ExecutorError) as excinfo:
        CliExecutor(_cfg(), query_fn=query).run("x", workspace="/w")
    assert "secret" not in str(excinfo.value)
    assert "leaked" not in str(excinfo.value)
