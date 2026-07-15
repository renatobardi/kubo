"""Spike 16.2 (C4 / ADR-0019) — prova o Claude Agent SDK DENTRO da imagem do Kubo.

DESCARTÁVEL: valida o MECANISMO antes do `CliExecutor` (16.3), não é código de
produção. Roda no kubo-test (LXC aninhado) para responder empiricamente as
Perguntas abertas do ADR-0019:

  1. o binário `claude` vendorizado pelo wheel (`claude_agent_sdk/_bundled/claude`,
     arch-casado por uv — sem Node separado na imagem) SPAWNA no LXC aninhado?
  2. um turno trivial COMPLETA (ResultMessage, is_error=False)? — exige ANTHROPIC_API_KEY.
  3. o custo real vem no stream (`total_cost_usd is not None`)? — decide se `budget_usd`
     entra no template (ADR-0019 §V / Pergunta aberta 2).
  4. ENV por WHITELIST de verdade: o SDK MERGE `os.environ` no subprocess
     (subprocess_cli.py:491), então a whitelist exige SCRUBBAR `os.environ` no spawn,
     não só passar `options.env`. O canário secreto do PAI NÃO pode vazar pro agente.

Uso (no servidor, dentro da imagem):
    docker compose run --rm -e ANTHROPIC_API_KEY kubo-scheduler python -m scripts.spike_cli
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

# Só estas sobrevivem no env do subprocess. ANTHROPIC_API_KEY paga o turno; PATH/HOME
# o binário precisa; LANG/LC_ALL evitam ruído de locale. Nada de SURREAL/TELEGRAM/PAT.
_WHITELIST = ("ANTHROPIC_API_KEY", "PATH", "HOME", "LANG", "LC_ALL")
_CANARY = "SPIKE_CANARY"
_CANARY_VALUE = "canary-must-not-leak-9f3a"


@contextmanager
def _scrubbed_environ() -> Iterator[None]:
    """os.environ = só a whitelist durante o spawn (o SDK snapshota os.environ no connect,
    subprocess_cli.py:491). Restaura no finally — o processo pai segue com seu env intacto."""
    saved = dict(os.environ)
    try:
        for key in list(os.environ):
            if key not in _WHITELIST:
                del os.environ[key]
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


async def _run() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("SPIKE FAIL: ANTHROPIC_API_KEY ausente", file=sys.stderr)
        return 2

    # Planta um segredo no env do PAI. Se a whitelist funciona, o agente não o vê.
    os.environ[_CANARY] = _CANARY_VALUE
    prompt = (
        "Run the shell command `printenv` once. Then reply with EXACTLY one word on one "
        f"line: CANARY_PRESENT if the output contains the text {_CANARY}, otherwise "
        "CANARY_ABSENT. Output nothing else."
    )
    workspace = tempfile.mkdtemp(prefix="kubo-spike-")
    options = ClaudeAgentOptions(
        cwd=workspace,
        permission_mode="bypassPermissions",  # headless (E6): contenção é env+cwd, não prompts
        max_turns=4,
        disallowed_tools=["WebFetch", "WebSearch"],  # E1: corta superfície de rede
        env={k: os.environ[k] for k in _WHITELIST if k in os.environ},
    )

    texts: list[str] = []
    result: ResultMessage | None = None
    with _scrubbed_environ():
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
            elif isinstance(msg, ResultMessage):
                result = msg

    verdict = " ".join(texts).upper()
    print("=== SPIKE 16.2 RESULT ===")
    print(f"turn_completed : {result is not None and not result.is_error}")
    print(f"num_turns      : {result.num_turns if result else '-'}")
    print(f"total_cost_usd : {result.total_cost_usd if result else '-'}")
    print(f"stop_reason    : {result.stop_reason if result else '-'}")
    print(f"agent_canary   : {'ABSENT' if 'CANARY_ABSENT' in verdict else 'PRESENT/UNCLEAR'}")
    print(f"agent_text     : {' '.join(texts)[:200]!r}")

    ok = (
        result is not None
        and not result.is_error
        and result.total_cost_usd is not None
        and "CANARY_ABSENT" in verdict
        and "CANARY_PRESENT" not in verdict
    )
    print(f"SPIKE {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
