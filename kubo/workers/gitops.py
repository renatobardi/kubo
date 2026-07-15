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
import os
import subprocess
from collections.abc import Callable

from kubo.errors import ConfigError, ForgeError

_Run = Callable[..., subprocess.CompletedProcess[str]]
_REDACTED = "<pat-redacted>"


def _run(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Executa `argv` (lista, sem shell). `env` extra é MERGEADO sobre `os.environ` SÓ neste
    subprocess (nunca exportado no processo pai) — é o canal do PAT no push (C2/§X)."""
    full_env = {**os.environ, **env} if env else None
    return subprocess.run(  # noqa: S603
        argv, capture_output=True, text=True, check=False, env=full_env
    )


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

    O PAT vira `Authorization: Basic base64("x-access-token:<PAT>")` num `http.extraHeader`
    entregue pelas vars `GIT_CONFIG_*` no ENV do subprocess do push (git ≥2.31) — NÃO no
    argv. Argv aparece em `ps`/crash-report/monitoring (superfície de LOG, colide com "nunca
    logar a credencial"); env exige leitura dirigida de `/proc/<pid>/environ` do mesmo UID
    (risco aceito da fase — §X). Não persiste no `.git/config` e a URL do push é limpa."""
    cred = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {cred}",
    }
    result = run(["git", "-C", workspace, "push", repo_url, f"HEAD:refs/heads/{branch}"], env=env)
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
