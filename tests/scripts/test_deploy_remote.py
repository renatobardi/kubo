"""`scripts/deploy-remote.sh`: o bloco remoto do deploy (modo CD, por digest).

Roda o script de verdade com um `docker` falso no PATH que registra cada chamada, num HOME
temporário (o script faz `cd ~/kubo` e lê o COMPOSE_FILE do `.env`). Os guards (digest/saúde) são
trocados por stubs: aqui o que importa é a ORDEM e o CONJUNTO de comandos do deploy.

Regressão nomeada: com o overlay `compose.central.yml` o banco é o da instância central, mas o
script subia `surrealdb` pelo nome, o que liga o profile `local-db` (reinicia o banco local a
cada deploy) e fazia o deploy esperar um banco que não é mais usado.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "deploy-remote.sh"
DIGEST = "ab" * 32  # 64 hex

_FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "${FAKE_DOCKER_LOG}"
case "$1" in
  pull) exit 0 ;;
  inspect)
    case "$*" in
      *RepoDigests*) echo "ghcr.io/renatobardi/kubo@sha256:__DIGEST__" ;;
      *Health.Status*) echo healthy ;;
    esac ;;
  compose)
    case "$*" in
      *"ps -q"*) echo containerid ;;
    esac ;;
  images) exit 0 ;;
esac
exit 0
""".replace("__DIGEST__", DIGEST)


def _deploy(tmp_path: Path, compose_file: str) -> list[str]:
    home = tmp_path / "home"
    project = home / "kubo"
    (project / "scripts").mkdir(parents=True)
    (project / ".env").write_text(f"COMPOSE_FILE={compose_file}\nOTHER=1\n")
    for guard in ("digest-guard.sh", "health-guard.sh"):
        (project / "scripts" / guard).write_text("#!/usr/bin/env bash\nexit 0\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text(_FAKE_DOCKER)
    fake.chmod(0o755)
    log = tmp_path / "docker.log"
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(home),
        "FAKE_DOCKER_LOG": str(log),
    }
    result = subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), f"sha256:{DIGEST}"],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return log.read_text().splitlines()


def _calls(lines: list[str], needle: str) -> list[str]:
    return [line for line in lines if needle in line]


def test_local_database_deploy_still_starts_and_waits_for_surrealdb(tmp_path: Path) -> None:
    """Sem o overlay central nada muda: o banco local é iniciado e esperado."""
    log = _deploy(tmp_path, "docker-compose.yml:compose.dev-lxc.yml")
    assert _calls(log, "up -d --no-build surrealdb")
    assert _calls(log, "ps -q surrealdb")


def test_central_overlay_never_touches_the_local_database(tmp_path: Path) -> None:
    log = _deploy(tmp_path, "docker-compose.yml:compose.dev-lxc.yml:compose.central.yml")
    assert not _calls(log, "up -d --no-build surrealdb")
    assert not _calls(log, "ps -q surrealdb")
    assert not [line for line in log if line.startswith("compose") and line.endswith("surrealdb")]


@pytest.mark.parametrize(
    "compose_file",
    [
        "docker-compose.yml:compose.dev-lxc.yml",
        "docker-compose.yml:compose.dev-lxc.yml:compose.central.yml",
    ],
)
def test_the_rest_of_the_deploy_runs_in_both_modes(tmp_path: Path, compose_file: str) -> None:
    """Migrations, seed, recriação dos serviços de app e a poda seguem iguais."""
    log = _deploy(tmp_path, compose_file)
    assert _calls(log, "stop kubo-api kubo-scheduler")
    assert _calls(log, "python -m kubo.store.migrations")
    assert _calls(log, "python -m kubo.store.seed")
    assert _calls(log, "up -d --no-build --force-recreate kubo-api kubo-scheduler")
    assert _calls(log, "image prune")
    # ordem: parar escritores -> migrations -> seed -> recriar
    order = [
        next(i for i, line in enumerate(log) if "stop kubo-api" in line),
        next(i for i, line in enumerate(log) if "kubo.store.migrations" in line),
        next(i for i, line in enumerate(log) if "kubo.store.seed" in line),
        next(i for i, line in enumerate(log) if "--force-recreate" in line),
    ]
    assert order == sorted(order)
