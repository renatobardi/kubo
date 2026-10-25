"""`scripts/ops/kubo-test-use-central.sh`: a edição do `.env` do servidor (código Python embutido).

Roda o trecho Python do próprio script contra um `.env` temporário. Regressão nomeada: o script
executa esse código via `lxc exec`, ou seja, como ROOT; o `.env` reescrito ficou `root:root 600` e
o usuário `ubuntu` (que roda o docker compose) passou a receber `permission denied`. O código tem
que devolver ao arquivo (e ao backup) o dono que o `.env` original tinha.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "kubo-test-use-central.sh"
_NEW = {
    "COMPOSE_FILE": "docker-compose.yml:compose.dev-lxc.yml",
    "SURREAL_URL": "ws://central:8000/rpc",
    "SURREAL_AUTH_LEVEL": "database",
    "SURREAL_PASS": "new-app-pass",  # pragma: allowlist secret
}


def _merge_code() -> str:
    match = re.search(r"MERGE_PY=\"\$\(cat <<'PY'\n(.*?)\nPY\n\)\"", SCRIPT.read_text(), re.S)
    assert match, "bloco MERGE_PY não encontrado no script"
    return match.group(1)


def _run(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    """Executa o código do script sobre `env_file`; devolve as chamadas a os.chown."""
    chowns: list[tuple[str, int, int]] = []
    monkeypatch.setattr(os, "chown", lambda p, uid, gid: chowns.append((str(p), uid, gid)))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_NEW)))
    code = _merge_code().replace("/home/ubuntu/kubo/.env", str(env_file))
    scope: dict[str, Any] = {"__name__": "merge"}
    exec(compile(code, "merge_py", "exec"), scope)  # noqa: S102
    return chowns


def _backup_path(env_file: Path) -> Path:
    """O backup mora FORA da pasta do .env: o rsync --delete do CD apaga o que não está no repo."""
    return env_file.parent.parent / ".env.pre-central"


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    (tmp_path / "kubo").mkdir()
    path = tmp_path / "kubo" / ".env"
    path.write_text(
        "COMPOSE_FILE=docker-compose.yml:compose.dev-lxc.yml\nSURREAL_PASS=old\nOTHER=1\n"
    )
    path.chmod(0o644)
    return path


def test_rewritten_env_and_backup_keep_the_original_owner(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = env_file.stat()
    chowns = _run(env_file, monkeypatch)
    owners = {Path(p).name: (uid, gid) for p, uid, gid in chowns}
    expected = (original.st_uid, original.st_gid)
    assert owners.get(".env.tmp") == expected, "o .env novo precisa manter o dono original"
    assert owners.get(".env.pre-central") == expected, "o backup também"
    assert (
        Path(next(p for p, _, _ in chowns if p.endswith(".env.pre-central"))).parent
        == env_file.parent.parent
    )


def test_env_ends_with_mode_600_and_keeps_unrelated_lines(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env_file, monkeypatch)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    text = env_file.read_text()
    assert "OTHER=1" in text
    assert text.count("compose.central.yml") == 1
    assert "SURREAL_PASS=new-app-pass" in text  # pragma: allowlist secret


def test_backup_is_kept_outside_the_directory_the_cd_syncs(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressão: com o backup em ~/kubo, o `rsync --delete` do CD o apagou no primeiro deploy."""
    _run(env_file, monkeypatch)
    assert _backup_path(env_file).exists()
    assert not (env_file.parent / ".env.pre-central").exists()
    assert "SURREAL_PASS=old" in _backup_path(env_file).read_text()  # pragma: allowlist secret


def test_second_run_is_idempotent_and_does_not_overwrite_the_backup(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(env_file, monkeypatch)
    first = env_file.read_text()
    backup = _backup_path(env_file).read_text()
    _run(env_file, monkeypatch)
    assert env_file.read_text() == first
    assert _backup_path(env_file).read_text() == backup
    assert "SURREAL_PASS=old" in backup  # pragma: allowlist secret


def _payload(env: dict[str, str]) -> dict[str, str]:
    """Executa o gerador do payload (o `python3 -c '...'` do script) com as variáveis dadas."""
    import subprocess

    match = re.search(r"python3 -c '\n(import json, os.*?\}\)\))'", SCRIPT.read_text(), re.S)
    assert match, "gerador do payload não encontrado no script"
    base = {"PATH": os.environ["PATH"], "CENTRAL_URL_": "ws://c:8000/rpc"}
    base.update(
        {"KUBO_CENTRAL_APP_PASS": "a", "KUBO_CENTRAL_RW_PASS": "b", "KUBO_CENTRAL_RO_PASS": "c"}
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", match.group(1)],
        env={**base, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_payload_defaults_to_the_dev_compose_files() -> None:
    payload = _payload({"CENTRAL_DB_": "test"})
    assert payload["COMPOSE_FILE"] == "docker-compose.yml:compose.dev-lxc.yml"


def test_payload_uses_the_base_compose_of_the_environment() -> None:
    """No prd o COMPOSE_FILE base é o do prd (só vale se o .env ainda não tiver a chave)."""
    payload = _payload(
        {"CENTRAL_DB_": "prd", "KUBO_BASE_COMPOSE_FILE": "docker-compose.yml:compose.prd-lxc.yml"}
    )
    assert payload["COMPOSE_FILE"] == "docker-compose.yml:compose.prd-lxc.yml"


def test_payload_builds_the_three_users_from_the_database_name() -> None:
    payload = _payload({"CENTRAL_DB_": "prd"})
    assert payload["SURREAL_DB"] == "prd"
    assert (
        payload["SURREAL_USER"],
        payload["KUBO_RW_SURREAL_USER"],
        payload["KUBO_RO_SURREAL_USER"],
    ) == (
        "app_prd",
        "rw_prd",
        "ro_prd",
    )
    assert payload["SURREAL_AUTH_LEVEL"] == "database"
