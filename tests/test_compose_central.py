"""Overlay `compose.central.yml`: o kubo usa a instância SurrealDB central (oute-surreal-db).

Testa o merge REAL (`docker compose config`), não o YAML cru: o overlay depende de `!reset` e de
profiles, que só o compose resolve. Pula se não houver docker compose.

O que precisa valer: (1) sem o overlay nada muda (o banco local segue sendo a dependência);
(2) com o overlay, scheduler e api falam com o endpoint da env e NÃO dependem do banco local, que
junto com o sidecar de backup (exporta do banco local) fica atrás do profile `local-db`;
(3) o rollback é ligar o profile `local-db`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CENTRAL_URL = "ws://10.173.117.50:8000/rpc"

# Variáveis fail-fast do compose base (valores de teste; nada disto é segredo real).
_ENV = {
    "SURREAL_PASS": "test-pass",  # pragma: allowlist secret
    "KUBO_RO_SURREAL_PASS": "test-ro",  # pragma: allowlist secret
    "KUBO_BREAKGLASS_TENANT_ID": "tenant:t",
    "KUBO_BREAKGLASS_USER_ID": "user:u",
    "KUBO_PASSWORD_HASH": "hash",  # pragma: allowlist secret
    "SESSION_SECRET": "session",  # pragma: allowlist secret
    "KUBO_SCHEDULER_TENANT_ID": "tenant:t",
    "KUBO_SCHEDULER_USER_UID": "uid",
    "SURREAL_URL": CENTRAL_URL,
}

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(  # noqa: S603
        ["docker", "compose", "version"],  # noqa: S607
        capture_output=True,
        check=False,
    ).returncode
    != 0,
    reason="docker compose indisponível",
)


def _config(*files: str, profiles: tuple[str, ...] = ()) -> dict[str, Any]:
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", f]
    for p in profiles:
        cmd += ["--profile", p]
    cmd += ["config", "--format", "json"]
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **_ENV}  # noqa: S108
    out = subprocess.run(  # noqa: S603
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_base_compose_still_depends_on_the_local_database() -> None:
    """Sem o overlay nada muda: o banco local sobe e o scheduler/api dependem dele saudável."""
    services = _config("docker-compose.yml")["services"]
    assert "surrealdb" in services
    for name in ("kubo-scheduler", "kubo-api"):
        assert services[name]["depends_on"]["surrealdb"]["condition"] == "service_healthy"


def test_central_overlay_drops_local_database_and_backup_sidecar() -> None:
    services = _config("docker-compose.yml", "compose.central.yml")["services"]
    assert set(services) == {"kubo-scheduler", "kubo-api"}


@pytest.mark.parametrize("name", ["kubo-scheduler", "kubo-api"])
def test_central_overlay_points_services_at_the_env_endpoint(name: str) -> None:
    service = _config("docker-compose.yml", "compose.central.yml")["services"][name]
    assert service["environment"]["SURREAL_URL"] == CENTRAL_URL
    assert "surrealdb" not in service.get("depends_on", {})


def test_central_overlay_requires_an_explicit_endpoint() -> None:
    """Sem SURREAL_URL o compose recusa subir (fail-fast, como os outros segredos/endpoints)."""
    saved = _ENV.pop("SURREAL_URL")
    try:
        with pytest.raises(AssertionError, match="SURREAL_URL"):
            _config("docker-compose.yml", "compose.central.yml")
    finally:
        _ENV["SURREAL_URL"] = saved


def test_local_db_profile_is_the_rollback_path() -> None:
    """`--profile local-db` traz de volta o banco local e o backup (volta ao desenho anterior)."""
    services = _config("docker-compose.yml", "compose.central.yml", profiles=("local-db",))[
        "services"
    ]
    assert {"surrealdb", "backup", "kubo-scheduler", "kubo-api"} <= set(services)
