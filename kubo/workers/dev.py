"""Worker dev — orquestra o executor `cli` + git/GitHub para produzir um PR (ADR-0019).

Worker de CONTRATO PLENO (ADR-0009): `manifest` + `run(ctx) -> RunResult`. O PR volta como
`PrPayload` no `RunResult` e é persistido pelo MESMO caminho do contrato (shape A) — nada de
segundo mecanismo. As primitivas seguras vivem em `gitops` (C2), `github_api` (E3) e no
`CliExecutor` (env whitelist + budget); este worker só as SEQUENCIA e mapeia o desfecho.
"""

from __future__ import annotations

import shutil
import tempfile

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kubo.contracts.models import ErrorInfo, PrPayload, RunResult, Stats, WorkerManifest
from kubo.contracts.worker import RunContext
from kubo.errors import ConfigError, ContractError, ForgeError
from kubo.executors.cli import CliOutcome, CliRunner
from kubo.workers import github_api, gitops

# Instrução OPERACIONAL do mecanismo (o framing de estilo é o prompt da persona): o agente
# COMMITA (o push é do worker) e NÃO abre PR (isso é fora do turno, com a credencial do worker).
_OPERATIONAL = (
    "\n\nVocê trabalha num clone local do repositório sandbox. Implemente a tarefa abaixo, "
    "rode os testes se houver, e COMMITE seu trabalho com git. NÃO faça push nem abra pull "
    "request — isso acontece fora do seu turno.\n\nTarefa:\n"
)


class DevConfig(BaseModel):
    """Config da task dev: a instrução do dono + as coordenadas do repo sandbox (D37).

    `instruction` é a task do DONO (confiável — dono cria tasks, ADR-0019); `branch` é
    derivado do flow id pelo behavior (único por construção — E5). `repo_url` é SEM
    credencial (C2). Identidade de commit obrigatória (E7)."""

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    instruction: str = Field(min_length=1, max_length=10000)
    repo_url: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    branch: str = Field(min_length=1, max_length=255)
    base_branch: str = "main"
    git_name: str = Field(min_length=1, max_length=100)
    git_email: str = Field(min_length=1, max_length=200)

    @field_validator("repo_url")
    @classmethod
    def _scheme(cls, value: str) -> str:
        """Exige esquema `https://`/`git@` — fecha a injeção de opção do git (um `repo_url`
        começando com `-` viraria flag em `git clone`/`push`, ex.: `--upload-pack=`)."""
        if not (value.startswith("https://") or value.startswith("git@")):
            raise ValueError("repo_url deve começar com https:// ou git@")
        return value

    @field_validator("branch")
    @classmethod
    def _no_leading_dash(cls, value: str) -> str:
        """Branch nunca começa com `-` (mesma defesa de injeção de opção no argv do git)."""
        if value.startswith("-"):
            raise ValueError("branch não pode começar com '-'")
        return value


class DevWorker:
    """Roda um turno do agente dev num clone efêmero e abre o PR (ADR-0019)."""

    manifest = WorkerManifest(
        name="dev", version="0.1.0", integrations=["github"], config=DevConfig
    )

    def __init__(self, executor: CliRunner, *, prompt: str) -> None:
        """Guarda o seam do executor cli e o prompt congelado da persona (framing do
        engenheiro). Sem I/O no construtor (precedente do AnalystWorker)."""
        self._executor = executor
        self._prompt = prompt

    def run(self, ctx: RunContext) -> RunResult:
        """Clona → roda o agente → confere diff (E5) → push (C2) → abre PR (E3).

        O workspace efêmero é removido no `finally` (E7 — senão o disco do LXC enche). Erro do
        agente (budget/timeout/agent) e diff vazio (E5) fecham a task SEM PR; `ForgeError`
        (git/GitHub) vira `ErrorInfo(kind="forge")` já sanitizado (o PAT nunca vaza)."""
        config = ctx.config
        if not isinstance(config, DevConfig):
            raise ContractError(
                f"DevWorker recebeu config {type(config).__name__}, esperava DevConfig"
            )
        github = ctx.integrations.get("github")
        if github is None or not github.secret:
            raise ConfigError("worker dev requer a integração github com PAT resolvido")
        pat = github.secret
        base_url = github.base_url or "https://api.github.com"
        log = ctx.logger.bind(worker="dev", branch=config.branch)

        workspace = tempfile.mkdtemp(prefix="kubo-dev-")
        outcome: CliOutcome | None = None
        try:
            gitops.clone(config.repo_url, workspace)  # C2: URL sem credencial
            gitops.configure_identity(workspace, name=config.git_name, email=config.git_email)
            base_sha = gitops.head_sha(workspace)
            gitops.create_branch(workspace, config.branch)
            outcome = self._executor.run(self._agent_prompt(config), workspace=workspace)
            stats = _stats(outcome)
            if outcome.error is not None:
                log.warning("dev_agent_failed", kind=outcome.error.kind)
                return RunResult(error=outcome.error, stats=stats)
            if not gitops.has_new_commits(workspace, base_sha):  # E5: nada a pushar
                return RunResult(error=_EMPTY, stats=stats)
            gitops.push(workspace, config.branch, repo_url=config.repo_url, token=pat)  # C2
            pr = github_api.open_pull_request(
                base_url=base_url,
                token=pat,
                owner=config.owner,
                repo=config.repo,
                head=config.branch,
                base=config.base_branch,
                title=_title(config),
                body=outcome.text,
            )
            log.info("dev_pr_opened", number=pr.number)
            return RunResult(
                payloads=[PrPayload(url=pr.url, number=pr.number, summary=outcome.text)],
                stats=stats,
            )
        except ForgeError as exc:
            # ForgeError já vem sanitizado (PAT redigido); str(exc) é seguro na fronteira.
            return RunResult(error=ErrorInfo(kind="forge", message=str(exc)), stats=_stats(outcome))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _agent_prompt(self, config: DevConfig) -> str:
        """Prompt do agente = framing da persona + instrução operacional + a task do dono."""
        return f"{self._prompt}{_OPERATIONAL}{config.instruction}"


_EMPTY = ErrorInfo(kind="empty", message="agente terminou sem diff — nada a pushar")


def _stats(outcome: CliOutcome | None) -> Stats:
    """Stats da run: custo e turnos reais do agente (auditoria de execução que custa $)."""
    if outcome is None:
        return Stats()
    return Stats.model_validate({"cost_usd": outcome.cost_usd, "num_turns": outcome.num_turns})


def _title(config: DevConfig) -> str:
    """Título do PR derivado da instrução do dono (que ENVIAMOS — não vem da API/agente)."""
    head = config.instruction.strip().splitlines()[0][:60]
    return f"[kubo dev] {head}"
