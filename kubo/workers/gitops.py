"""Operações git no workspace efêmero do worker dev (ADR-0019 §III/§VIII).

C2 — clone SEM credencial: o remote do workspace é sempre URL sem PAT (o agente lê
`.git/config` com `cat`). O PAT entra SÓ no push, via `http.extraHeader` por comando
(`git -c ...`), nunca `remote set-url`/URL embutida — logo nunca cai no `.git/config`, e
nunca é logado. Erro de git redige o PAT (belt-and-suspenders) como o sender do Telegram.

Todas as funções recebem `run` injetável (fake em teste); a única chamada real de
subprocess mora em `_run`, com argv em LISTA (sem shell, sem injeção).
"""

from __future__ import annotations

import base64
import subprocess
from collections.abc import Callable

from kubo.errors import ConfigError, ForgeError

_Run = Callable[[list[str]], subprocess.CompletedProcess[str]]
_REDACTED = "<pat-redacted>"


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Executa `argv` (lista, sem shell) e devolve o resultado; o caller inspeciona rc."""
    return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603


def clone(repo_url: str, workspace: str, *, run: _Run = _run) -> None:
    """Clona `repo_url` (SEM credencial — C2) em `workspace`. `--` fecha fim-de-opções (o
    `repo_url` já é validado em DevConfig, mas o marcador é defesa em profundidade barata)."""
    _check(run(["git", "clone", "--", repo_url, workspace]), "clone")


def configure_identity(workspace: str, *, name: str, email: str, run: _Run = _run) -> None:
    """Fixa user.name/email no workspace; trava (ConfigError) se algum faltar (E7)."""
    if not name or not email:
        raise ConfigError("identidade de commit ausente: name e email são obrigatórios (E7)")
    _check(run(["git", "-C", workspace, "config", "user.name", name]), "config user.name")
    _check(run(["git", "-C", workspace, "config", "user.email", email]), "config user.email")


def create_branch(workspace: str, branch: str, *, run: _Run = _run) -> None:
    """Cria e faz checkout de `branch` (derivado do flow id — único por construção)."""
    _check(run(["git", "-C", workspace, "checkout", "-b", branch]), "checkout -b")


def head_sha(workspace: str, *, run: _Run = _run) -> str:
    """SHA do HEAD do workspace."""
    result = run(["git", "-C", workspace, "rev-parse", "HEAD"])
    _check(result, "rev-parse")
    return result.stdout.strip()


def has_new_commits(workspace: str, base_sha: str, *, run: _Run = _run) -> bool:
    """True se o HEAD avançou além de `base_sha` (o agente commitou algo — E5)."""
    return head_sha(workspace, run=run) != base_sha


def push(workspace: str, branch: str, *, repo_url: str, token: str, run: _Run = _run) -> None:
    """Empurra `HEAD` para `branch` no `repo_url`, com o PAT injetado só aqui (C2).

    O PAT vira `Authorization: Basic base64("x-access-token:<PAT>")` num `-c http.extraHeader`
    — vale só para este comando, não persiste no `.git/config`, e a URL do push é limpa."""
    cred = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    header = f"http.extraHeader=Authorization: Basic {cred}"
    result = run(
        ["git", "-C", workspace, "-c", header, "push", repo_url, f"HEAD:refs/heads/{branch}"]
    )
    _check(result, "push", secrets=(token, cred))


def _check(
    result: subprocess.CompletedProcess[str], op: str, *, secrets: tuple[str, ...] = ()
) -> None:
    """Levanta `ForgeError` se o git falhou, redigindo qualquer `secrets` do stderr (§VIII)."""
    if result.returncode == 0:
        return
    detail = (result.stderr or "").strip()
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, _REDACTED)
    raise ForgeError(f"git {op} falhou (rc={result.returncode}): {detail[:200]}")
