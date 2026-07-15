"""Testes do `gitops` (ADR-0019 §III/§VIII, C2/E5/E7).

Duas camadas: (1) unit com um `run` fake que grava o argv — prova a INJEÇÃO de credencial
do push (C2: PAT no `http.extraHeader`, URL sem credencial, nunca `remote set-url`) e a
redação em erro; (2) um end-to-end com git real em repos locais — prova que o argv funciona
e que o PAT NUNCA cai no `.git/config`.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kubo.errors import ConfigError, ForgeError
from kubo.workers import gitops

_FAKE_PAT = "fake-forge-pat-do-not-leak"  # não é um PAT real (evita o gate detect-secrets)


def _rec(handler: Any = None) -> tuple[Any, list[list[str]]]:
    """`run` fake: grava cada argv e devolve (rc, stdout, stderr) do `handler` (default ok)."""
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        rc, out, err = handler(argv) if handler else (0, "", "")
        return subprocess.CompletedProcess(argv, rc, out, err)

    return run, calls


def test_push_injects_pat_in_header_not_url(monkeypatch: pytest.MonkeyPatch) -> None:
    run, calls = _rec()
    gitops.push(
        "/ws",
        "kubo/flow-1",
        repo_url="https://github.com/owner/kubo-forge.git",
        token=_FAKE_PAT,
        run=run,
    )
    argv = calls[0]
    assert argv[:3] == ["git", "-C", "/ws"]
    # PAT vai no http.extraHeader (Basic base64("x-access-token:PAT")), NUNCA na URL
    idx = argv.index("-c")
    header = argv[idx + 1]
    assert header.startswith("http.extraHeader=Authorization: Basic ")
    creds = base64.b64decode(header.split("Basic ", 1)[1]).decode()
    assert creds == f"x-access-token:{_FAKE_PAT}"
    # a URL do push é limpa (sem credencial embutida — C2)
    url_arg = argv[argv.index("push") + 1]
    assert url_arg == "https://github.com/owner/kubo-forge.git"
    assert _FAKE_PAT not in url_arg
    # jamais persiste credencial: sem remote set-url / credential helper
    assert not any("set-url" in a or "credential" in a for a in argv)
    # empurra o branch derivado do flow id, explícito (não `--all`, não `HEAD`)
    assert argv[-1] == "HEAD:refs/heads/kubo/flow-1"


def test_push_error_redacts_pat() -> None:
    run, _ = _rec(lambda argv: (128, "", f"fatal: auth failed for {_FAKE_PAT} at remote"))
    with pytest.raises(ForgeError) as excinfo:
        gitops.push("/ws", "b", repo_url="https://x/r.git", token=_FAKE_PAT, run=run)
    assert _FAKE_PAT not in str(excinfo.value)


def test_configure_identity_requires_name_and_email() -> None:
    run, _ = _rec()
    with pytest.raises(ConfigError):
        gitops.configure_identity("/ws", name="", email="dev@kubo.local", run=run)
    with pytest.raises(ConfigError):
        gitops.configure_identity("/ws", name="Kubo Dev", email="", run=run)


def test_has_new_commits_compares_head_to_base() -> None:
    run_moved, _ = _rec(lambda argv: (0, "newsha\n", "") if "rev-parse" in argv else (0, "", ""))
    assert gitops.has_new_commits("/ws", "basesha", run=run_moved) is True
    run_same, _ = _rec(lambda argv: (0, "basesha\n", ""))
    assert gitops.has_new_commits("/ws", "basesha", run=run_same) is False


def test_git_command_failure_raises_forge_error() -> None:
    run, _ = _rec(lambda argv: (1, "", "fatal: not a git repository"))
    with pytest.raises(ForgeError):
        gitops.head_sha("/ws", run=run)


@pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")
def test_real_git_end_to_end_keeps_pat_out_of_config(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _seed_main(tmp_path / "seed", remote)

    ws = tmp_path / "ws"
    gitops.clone(str(remote), str(ws))
    gitops.configure_identity(str(ws), name="Kubo Dev", email="dev@kubo.local")
    base = gitops.head_sha(str(ws))
    gitops.create_branch(str(ws), "kubo/flow-1")

    # o "agente" edita e commita
    (ws / "work.txt").write_text("done", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "."], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-m", "agent work"], check=True)
    assert gitops.has_new_commits(str(ws), base) is True

    gitops.push(str(ws), "kubo/flow-1", repo_url=str(remote), token=_FAKE_PAT)

    # C2: o PAT nunca cai no .git/config
    assert _FAKE_PAT not in (ws / ".git" / "config").read_text(encoding="utf-8")
    # o branch chegou no remote
    branches = subprocess.run(
        ["git", "-C", str(remote), "branch", "--list", "kubo/flow-1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "kubo/flow-1" in branches.stdout


def _seed_main(seed: Path, remote: Path) -> None:
    """Semeia o bare `remote` com um commit inicial em main (via um clone descartável)."""
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True)
    (seed / "README.md").write_text("seed", encoding="utf-8")
    ident = ["-c", "user.email=seed@kubo.local", "-c", "user.name=Seed"]
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), *ident, "commit", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "origin", "main"], check=True)
