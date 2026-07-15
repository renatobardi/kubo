"""Executor `cli` — Claude Code via Claude Agent SDK (ADR-0019).

Seam SEPARADO do `Executor.complete` (api, single-shot): contrato "prompt in → stream de
eventos out" (subprocess agêntico). A separação de tipos é FRONTEIRA DE SEGURANÇA — um
worker do circuito de conteúdo coletado nunca pode receber, por engano de wiring, um
executor com filesystem/bash (ADR-0019 §I).

Env por WHITELIST de verdade (ADR-0019 §IV): o SDK faz MERGE de `os.environ` no subprocess
(`subprocess_cli.py:491`, SDK pinado ==0.2.119) e `options.env` só sobrepõe. Então a
whitelist real exige SCRUBBAR `os.environ` na janela do spawn — `options.env` sozinho não
remove segredos. Uma única fonte de verdade (`_whitelist_env`) alimenta as DUAS camadas:
o scrub (subtração de SURREAL/TELEGRAM/PAT) e o `options.env` (override de HOME→workspace,
que sobrevive a drift de timing do SDK). Bump do SDK exige re-rodar o canário de env
(`tests/executors/test_cli.py::test_env_whitelist_scrubs_parent_secrets`).

Teto nomeado (ADR-0019 §X): o scrub de `os.environ` process-wide só é válido AQUI — no
processo do CLI (`kubo flow run`), síncrono, um flow por vez, sem trabalho env-dependente
concorrente. Se o flow dev migrar para dentro do `kubo-api`/scheduler, sobe para
processo-filho com env limpo (converge com o gatilho (b) do ADR-0018).
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Iterator
from contextlib import aclosing, contextmanager
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk import query as _sdk_query
from pydantic import BaseModel, ConfigDict

from kubo.contracts.models import ErrorInfo
from kubo.errors import ExecutorError

# Só estas atravessam a whitelist. Segredos (SURREAL/TELEGRAM/PAT) ficam de fora POR
# AUSÊNCIA — a whitelist é positiva, não uma lista negra que envelhece.
_PARENT_KEYS = ("ANTHROPIC_API_KEY", "PATH", "LANG", "LC_ALL")

# Mensagens FIXAS e genéricas (§VIII): nunca embutem a saída crua do agente nem o corpo do
# erro do SDK (que pode carregar caminhos/segredos).
_BUDGET_MSG = "custo do turno estourou o budget do flow"
_TIMEOUT_MSG = "turno do agente excedeu o timeout de wall-clock"
_AGENT_MSG = "turno do agente falhou"
_SPAWN_MSG = "falha ao iniciar o executor cli"


def _whitelist_env(workspace: str) -> dict[str, str]:
    """Mapping da whitelist — FONTE ÚNICA das duas camadas de enforcement de env (Q2).

    `ANTHROPIC_API_KEY`/`PATH`/`LANG`/`LC_ALL` vêm do pai; `HOME` aponta pro workspace
    efêmero (config/cache do agente não vazam pro HOME real). Sem `os.environ` do pai:
    quem subtrai os segredos é o scrub que consome este mapping, não este dict."""
    env = {key: os.environ[key] for key in _PARENT_KEYS if key in os.environ}
    env["HOME"] = workspace
    return env


# O scrub muta `os.environ` (global do processo): o lock torna o teto de reentrância do §X
# CONSTRUÇÃO, não comentário — duas entradas concorrentes sobrescreveriam o `saved` uma da
# outra e o restore devolveria um snapshot desatualizado (vazamento cruzado). Falha alto.
_SCRUB_LOCK = threading.Lock()


@contextmanager
def _scrubbed_environ(env: dict[str, str]) -> Iterator[None]:
    """`os.environ` = só `env` durante o bloco; restaura INTEGRALMENTE no finally.

    Snapshot → clear → whitelist → yield → clear + restore (advisor condição 2): restore
    seletivo deixaria o pai com env mutilado numa exceção no meio, e o push do PAT (no
    processo pai, depois) falharia de forma misteriosa. Reentrância (§X) FALHA ALTO — o
    scrub só vale no processo síncrono do CLI, um flow por vez; concorrência exige
    processo-filho com env limpo, não este swap global."""
    if not _SCRUB_LOCK.acquire(blocking=False):
        raise RuntimeError(
            "_scrubbed_environ reentrado — scrub de os.environ é process-wide e só vale no "
            "processo síncrono do CLI (ADR-0019 §X); flow dev concorrente exige processo-filho"
        )
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
        _SCRUB_LOCK.release()


class CliExecutorConfig(BaseModel):
    """Configuração do `CliExecutor`. `extra="forbid"` fecha a superfície (nenhum campo
    espúrio entra por acidente); `model` vem da persona, `budget_usd` do snapshot do flow."""

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    model: str
    budget_usd: float
    max_turns: int = 30
    timeout_s: float = 600.0
    disallowed_tools: tuple[str, ...] = ("WebFetch", "WebSearch")


class CliOutcome(BaseModel):
    """Resultado estruturado de um turno do agente. `text` é a PROSA do agente (untrusted,
    renderizada como texto plano no gate — E4); dados estruturais (URL do PR etc.) NUNCA
    vêm daqui (E3). `error` != None sinaliza budget/timeout/agent sem levantar exceção."""

    model_config = ConfigDict(extra="forbid")

    text: str
    cost_usd: float
    num_turns: int
    stop_reason: str | None = None
    error: ErrorInfo | None = None


class CliRunner(Protocol):
    """Seam mínimo que o worker dev recebe (fake em teste; nunca o concreto — ADR-0019 §I)."""

    def run(self, prompt: str, *, workspace: str) -> CliOutcome: ...


class CliExecutor:
    """Roda o agente Claude Code num workspace pinado, com env por whitelist e backstops
    de budget/turnos/wall-clock (ADR-0019 §IV/§V)."""

    def __init__(self, config: CliExecutorConfig, *, query_fn: Any = _sdk_query) -> None:
        """Guarda a config e o seam `query_fn` (injetável para teste); não faz I/O."""
        self._config = config
        self._query = query_fn

    def run(self, prompt: str, *, workspace: str) -> CliOutcome:
        """Roda um turno do agente e devolve o resultado estruturado.

        Síncrono por construção (workers são síncronos): dirige o `query` async via
        `asyncio.run`. Teto: se algum dia for chamado de dentro de um event loop vivo,
        troca a ponte (o scrub de env process-wide também exigiria revisão — ver §X)."""
        return asyncio.run(self._arun(prompt, workspace))

    async def _arun(self, prompt: str, workspace: str) -> CliOutcome:
        """Constrói env+options, roda o turno sob scrub+timeout, mapeia o desfecho."""
        env = _whitelist_env(workspace)
        options = self._build_options(workspace, env)
        try:
            with _scrubbed_environ(env):
                texts, result = await asyncio.wait_for(
                    self._collect(prompt, options), timeout=self._config.timeout_s
                )
        except TimeoutError:
            return CliOutcome(
                text="",
                cost_usd=0.0,
                num_turns=0,
                error=ErrorInfo(kind="timeout", message=_TIMEOUT_MSG),
            )
        except ClaudeSDKError:
            # QUALQUER erro do SDK (binário ausente, conexão, E **ProcessError** de saída
            # != 0 — cujo `str()` embute o STDERR cru do CLI) vira ExecutorError genérico,
            # `from None`: o corpo cru NUNCA cruza a fronteira (§VIII). Capturar só as
            # subclasses de infra deixaria o ProcessError (irmão, não subclasse) escapar
            # e vazar o stderr até `run.error`/UI (achado de security-review).
            raise ExecutorError(_SPAWN_MSG) from None
        return self._build_outcome(texts, result)

    def _build_options(self, workspace: str, env: dict[str, str]) -> ClaudeAgentOptions:
        """Options do turno: cwd pinado, modelo da persona, backstops nativos (max_turns,
        max_budget_usd), superfície de rede cortada (disallowed_tools), headless (E6), e
        `env` = MESMA whitelist do scrub (2ª camada, Q2)."""
        return ClaudeAgentOptions(
            cwd=workspace,
            model=self._config.model,
            max_turns=self._config.max_turns,
            max_budget_usd=self._config.budget_usd,
            disallowed_tools=list(self._config.disallowed_tools),
            permission_mode="bypassPermissions",
            env=env,
        )

    async def _collect(
        self, prompt: str, options: ClaudeAgentOptions
    ) -> tuple[list[str], ResultMessage | None]:
        """Consome o stream: acumula a prosa (TextBlocks) e guarda o ResultMessage final.

        `aclosing` garante o `aclose` do gerador em qualquer saída (fim, exceção, cancel do
        timeout) — sem isso, um timeout deixaria o subprocess do CLI órfão."""
        texts: list[str] = []
        result: ResultMessage | None = None
        async with aclosing(self._query(prompt=prompt, options=options)) as stream:
            async for message in stream:
                if isinstance(message, AssistantMessage):
                    texts += [b.text for b in message.content if isinstance(b, TextBlock)]
                elif isinstance(message, ResultMessage):
                    result = message
        return texts, result

    def _build_outcome(self, texts: list[str], result: ResultMessage | None) -> CliOutcome:
        """Mapeia (prosa, ResultMessage) → CliOutcome, com o check DETERMINÍSTICO de budget.

        Budget: check próprio pós-turno (`total_cost_usd > budget_usd`) — 5 linhas,
        testável com SDK mockado, produz o `ErrorInfo(kind="budget")` do E2; o
        `max_budget_usd` nativo é a camada extra (não determinística, não aposenta este).
        Overshoot ≤ 1 turno é aceito (o custo chega DEPOIS do gasto — ADR-0019 §V)."""
        text = "\n".join(texts)
        if result is None:
            # Stream terminou sem ResultMessage: o turno não fechou honestamente.
            return CliOutcome(
                text=text,
                cost_usd=0.0,
                num_turns=0,
                error=ErrorInfo(kind="agent", message=_AGENT_MSG),
            )
        cost = result.total_cost_usd or 0.0
        error: ErrorInfo | None = None
        if cost > self._config.budget_usd:
            error = ErrorInfo(
                kind="budget",
                message=_BUDGET_MSG,
                detail={"cost_usd": cost, "budget_usd": self._config.budget_usd},
            )
        elif result.is_error:
            error = ErrorInfo(kind="agent", message=_AGENT_MSG)
        return CliOutcome(
            text=text,
            cost_usd=cost,
            num_turns=result.num_turns,
            stop_reason=result.stop_reason,
            error=error,
        )
