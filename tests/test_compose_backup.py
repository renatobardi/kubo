"""Testes do sidecar de backup do compose (ADR-0011 §VI, emenda 2026-10-01).

O comando do sidecar roda num diretório temporário, com `wget` e `sleep`
trocados por stubs: prova a cadência e a retenção pelo que acontece com os
dumps, não pelo texto do YAML. Roda no `/bin/sh` e no `find` do host, não no
busybox do alpine: a semântica do busybox não é exercitada aqui.

Regressão nomeada: cada `/export` deixa o SurrealDB v3.1.5 com ~140 MiB de
memória a mais; com dump diário o kubo-test bateu no teto de 3 GiB do LXC
(renatobardi/kubo#252). A cadência passa a ser por ambiente.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY = 86400


def _compose(name: str) -> dict[str, Any]:
    with open(REPO_ROOT / name) as f:
        return yaml.safe_load(f)


def _backup_env(*files: str) -> dict[str, str]:
    """Environment do serviço `backup` depois do merge dos arquivos, como o compose faz."""
    env: dict[str, str] = {}
    for name in files:
        service = _compose(name).get("services", {}).get("backup", {})
        env.update({k: str(v) for k, v in service.get("environment", {}).items()})
    return env


def _default(value: str) -> str:
    """`${VAR:-padrão}` -> `padrão`; valor literal passa direto."""
    if value.startswith("${") and ":-" in value:
        return value[2:-1].split(":-", 1)[1]
    return value


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _run_one_cycle(
    tmp_path: Path, *, export_ok: bool = True, env: dict[str, str] | None = None
) -> tuple[Path, str, str]:
    """Roda uma volta do loop; devolve (dir dos dumps, stdout, argumento do sleep)."""
    backups = tmp_path / "backups"
    backups.mkdir(exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    sleep_log = tmp_path / "sleep.log"
    # -O é o 5º argumento do wget no compose; o stub grava ali ou falha.
    _stub(
        bindir / "wget",
        'out=""; while [ $# -gt 0 ]; do [ "$1" = "-O" ] && out="$2"; shift; done\n'
        + ('echo "-- dump" > "$out"' if export_ok else 'echo partial > "$out"; exit 4'),
    )
    # O sleep encerra o loop: uma volta por teste.
    _stub(bindir / "sleep", f'echo "$1" > "{sleep_log}"; kill -TERM $PPID')
    command = _compose("docker-compose.yml")["services"]["backup"]["command"]
    assert command[:2] == ["sh", "-c"]
    script = command[2].replace("$$", "$").replace("/backups", str(backups))
    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", script],
        env={
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "SURREAL_USER": "u",
            "SURREAL_PASS": "p",
            "SURREAL_NS": "kubo",
            "SURREAL_DB": "kubo",
            "BACKUP_INTERVAL_SECONDS": str(7 * DAY),
            "BACKUP_RETENTION_DAYS": "35",
            **(env or {}),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    slept = sleep_log.read_text().strip() if sleep_log.exists() else ""
    return backups, result.stdout + result.stderr, slept


def _old_dump(backups: Path, name: str, days: int) -> Path:
    backups.mkdir(exist_ok=True)
    path = backups / name
    path.write_text("-- old dump\n")
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_sleeps_the_configured_interval(tmp_path: Path) -> None:
    _, _, slept = _run_one_cycle(tmp_path, env={"BACKUP_INTERVAL_SECONDS": "7200"})
    assert slept == "7200"


@pytest.mark.parametrize("bad", ["", "abc", "0", "60", "7d"])
def test_an_invalid_interval_falls_back_instead_of_spinning(tmp_path: Path, bad: str) -> None:
    """Um `sleep` que falha deixaria o loop batendo no /export sem pausa — o
    mesmo problema que a cadência veio resolver."""
    _, output, slept = _run_one_cycle(tmp_path, env={"BACKUP_INTERVAL_SECONDS": bad})
    assert slept == str(7 * DAY)
    assert "BACKUP_INTERVAL_SECONDS inválido" in output


def test_an_invalid_retention_falls_back_instead_of_keeping_everything(tmp_path: Path) -> None:
    stale = _old_dump(tmp_path / "backups", "kubo-stale.surql", days=40)
    _, output, _ = _run_one_cycle(tmp_path, env={"BACKUP_RETENTION_DAYS": "um mês"})
    assert "BACKUP_RETENTION_DAYS inválido" in output
    assert not stale.exists()


def test_a_good_export_leaves_a_dump(tmp_path: Path) -> None:
    backups, output, _ = _run_one_cycle(tmp_path)
    assert len(list(backups.glob("kubo-*.surql"))) == 1
    assert "dump local" in output


def test_prunes_only_dumps_older_than_the_retention(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    stale = _old_dump(backups, "kubo-stale.surql", days=40)
    kept = _old_dump(backups, "kubo-kept.surql", days=20)
    other = _old_dump(backups, "notes.txt", days=400)

    _run_one_cycle(tmp_path, env={"BACKUP_RETENTION_DAYS": "35"})

    assert not stale.exists()
    assert kept.exists()
    assert other.exists()


def test_a_failed_export_deletes_no_older_dump(tmp_path: Path) -> None:
    """Com cadência mensal, o dump anterior já passou da retenção quando o novo
    é tentado: se o export falha e a retenção roda mesmo assim, não sobra nenhum."""
    backups = tmp_path / "backups"
    last_good = _old_dump(backups, "kubo-last-good.surql", days=40)

    _, output, slept = _run_one_cycle(
        tmp_path, export_ok=False, env={"BACKUP_RETENTION_DAYS": "35"}
    )

    assert "FALHOU" in output
    assert last_good.exists()
    assert list(backups.glob("kubo-*.surql")) == [last_good]
    assert slept  # o loop segue para o próximo ciclo


def test_no_environment_dumps_daily_anymore() -> None:
    base = int(_default(_backup_env("docker-compose.yml")["BACKUP_INTERVAL_SECONDS"]))
    assert base >= 7 * DAY


@pytest.mark.parametrize(
    ("overlay", "interval_days"),
    [("compose.dev-lxc.yml", 30), ("compose.prd-lxc.yml", 7)],
)
def test_cadence_per_environment(overlay: str, interval_days: int) -> None:
    """kubo-test (DEV) mensal, kubo-prd semanal — decisão do dono, 2026-10-01."""
    env = _backup_env("docker-compose.yml", overlay)
    assert int(_default(env["BACKUP_INTERVAL_SECONDS"])) == interval_days * DAY


@pytest.mark.parametrize(
    "files",
    [
        ("docker-compose.yml",),
        ("docker-compose.yml", "compose.dev-lxc.yml"),
        ("docker-compose.yml", "compose.prd-lxc.yml"),
    ],
)
def test_retention_keeps_at_least_three_dumps(files: tuple[str, ...]) -> None:
    env = _backup_env(*files)
    interval_days = int(_default(env["BACKUP_INTERVAL_SECONDS"])) / DAY
    retention_days = int(_default(env["BACKUP_RETENTION_DAYS"]))
    assert retention_days >= 3 * interval_days
