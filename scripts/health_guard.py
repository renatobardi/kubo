#!/usr/bin/env python3
"""health_guard.py — falha o deploy se o serviço recém-criado não ficar de pé.

Uso:
    python3 scripts/health_guard.py <service> --mode healthy [--timeout 90]
    python3 scripts/health_guard.py <service> --mode stable [--timeout 90] [--stable-for 15]

Incidente 2026-07-30: ``docker compose up -d`` retorna antes do serviço estar
saudável — um ConfigError de env faltante deixou o kubo-api em crash-loop nos
DOIS ambientes e o job de CD declarou sucesso. Mesma família do incidente do
BUILD_ID (PR #38): o deploy só termina OK quando o container recém-criado
prova que está vivo.

Modos:
  healthy — espera ``.State.Health.Status == healthy`` (serviços COM healthcheck,
            ex.: kubo-api). Falha rápido em unhealthy/exited/dead/restarting.
  stable  — serviços SEM healthcheck (kubo-scheduler; decisão do compose:
            BlockingScheduler sem porta não tem check honesto). O check honesto
            possível: o container recém-criado (force-recreate → RestartCount 0)
            fica ``running`` por N segundos seguidos sem reiniciar; crash-loop
            de ConfigError derruba o processo em segundos e incrementa o contador.

Como o digest_guard, roda no host bare-metal (fora do venv da imagem): só stdlib,
sem imports de outros módulos do repo (executado como ARQUIVO, não como pacote).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

_monotonic = time.monotonic
_sleep = time.sleep

_HEX_RE = re.compile(r"^[a-f0-9]+$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Estados terminais/cíclicos: sob restart policy, `restarting` só existe após um
# crash — qualquer um destes é falha imediata, sem esperar o timeout.
_DEAD_STATES = frozenset({"restarting", "exited", "dead"})


def _log_event(event: str, level: str, **fields: object) -> None:
    """Emite um evento estruturado em JSON no stderr (só stdlib, sem structlog)."""
    payload = {"event": event, "level": level, "worker": "health-guard", **fields}
    print(json.dumps(payload), file=sys.stderr)


def run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Wrapper fino em torno de subprocess.run para facilitar testes."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)  # noqa: S603


def _container_id(service: str, project: str) -> str:
    """Retorna o id do container do serviço, ou levanta RuntimeError."""
    result = run(["docker", "compose", "--project-name", project, "ps", "-q", service])
    if result.returncode != 0:
        raise RuntimeError(f"docker compose ps falhou para '{service}': {result.stderr.strip()}")
    container_id = result.stdout.strip().split("\n")[0]
    if not container_id:
        raise RuntimeError(f"nenhum container para o serviço '{service}'")
    if not _HEX_RE.fullmatch(container_id):
        raise RuntimeError(f"id de container inválido: '{container_id}'")
    return container_id


def _inspect_state(container_id: str, fmt: str) -> str:
    """docker inspect --format ``fmt`` do container; levanta RuntimeError se falhar."""
    result = run(["docker", "inspect", "--format", fmt, container_id])
    if result.returncode != 0:
        raise RuntimeError(f"docker inspect falhou: {result.stderr.strip()}")
    return result.stdout.strip()


def _validate_names(service: str, project: str) -> str | None:
    """Valida nomes de serviço/projeto; retorna mensagem de erro ou None."""
    if not _NAME_RE.fullmatch(service):
        return f"[health-guard] FALHOU: nome de serviço inválido: '{service}'"
    if not _NAME_RE.fullmatch(project):
        return f"[health-guard] FALHOU: nome de projeto inválido: '{project}'"
    return None


def wait_healthy(
    service: str,
    project: str = "kubo",
    timeout: float = 90,
    interval: float = 3,
) -> tuple[int, str]:
    """Espera o healthcheck do container do serviço reportar ``healthy``.

    Retorna ``(0, mensagem)`` quando healthy; ``(1, mensagem)`` em crash
    (restarting/exited/dead/unhealthy), serviço sem healthcheck, container
    ausente ou timeout.
    """
    if invalid := _validate_names(service, project):
        return 1, invalid
    try:
        container_id = _container_id(service, project)
    except RuntimeError as exc:
        return 1, f"[health-guard] FALHOU: {exc}"

    fmt = "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}"
    deadline = _monotonic() + timeout
    while True:
        try:
            parts = _inspect_state(container_id, fmt).split()
        except RuntimeError as exc:
            return 1, f"[health-guard] FALHOU: {exc}"
        status = parts[0] if parts else ""
        health = parts[1] if len(parts) > 1 else ""
        if health == "healthy":
            return 0, f"[health-guard] OK: {service} healthy"
        if not health:
            return (
                1,
                f"[health-guard] FALHOU: serviço '{service}' não tem healthcheck"
                " — use --mode stable",
            )
        if health == "unhealthy" or status in _DEAD_STATES:
            return (
                1,
                f"[health-guard] FALHOU: {service} está '{status}' (health '{health}')"
                " — container não ficou de pé (crash-loop?)",
            )
        if _monotonic() >= deadline:
            return (
                1,
                f"[health-guard] FALHOU: {service} não ficou healthy em {timeout:g}s"
                f" (status '{status}', health '{health}')",
            )
        _sleep(interval)


def _stable_failure(service: str, status: str, restarts: int) -> str | None:
    """Falha imediata do modo stable (crash contado ou estado morto), ou None."""
    if restarts > 0:
        return (
            f"[health-guard] FALHOU: {service} já reiniciou {restarts} vez(es)"
            " após o deploy — crash-loop (restart)"
        )
    if status in _DEAD_STATES:
        return f"[health-guard] FALHOU: {service} está '{status}' — container não ficou de pé"
    return None


def wait_stable(
    service: str,
    project: str = "kubo",
    timeout: float = 90,
    stable_for: float = 15,
    interval: float = 3,
) -> tuple[int, str]:
    """Espera o container (sem healthcheck) ficar ``running`` estável, sem restart.

    Sucesso = ``running`` contínuo por ``stable_for`` segundos com
    ``RestartCount == 0`` (o deploy recria o container, então qualquer restart
    é crash). Falha em restarting/exited/dead, restart contado ou timeout.
    """
    if invalid := _validate_names(service, project):
        return 1, invalid
    try:
        container_id = _container_id(service, project)
    except RuntimeError as exc:
        return 1, f"[health-guard] FALHOU: {exc}"

    fmt = "{{.State.Status}} {{.State.RestartCount}}"
    deadline = _monotonic() + timeout
    running_since: float | None = None
    while True:
        try:
            parts = _inspect_state(container_id, fmt).split()
        except RuntimeError as exc:
            return 1, f"[health-guard] FALHOU: {exc}"
        status = parts[0] if parts else ""
        restarts = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if failure := _stable_failure(service, status, restarts):
            return 1, failure
        now = _monotonic()
        if status == "running":
            if running_since is None:
                running_since = now
            if now - running_since >= stable_for:
                return 0, f"[health-guard] OK: {service} running estável há {stable_for:g}s"
        else:
            running_since = None
        if now >= deadline:
            return (
                1,
                f"[health-guard] FALHOU: {service} não ficou estável em {timeout:g}s"
                f" (status '{status}')",
            )
        _sleep(interval)


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI: espera o serviço ficar de pé conforme o modo."""
    parser = argparse.ArgumentParser(
        description="Falha (exit 1) se o serviço compose recém-criado não ficar de pé."
    )
    parser.add_argument("service", help="Nome do serviço no docker compose")
    parser.add_argument(
        "--mode",
        choices=["healthy", "stable"],
        required=True,
        help="healthy = espera o healthcheck; stable = running estável sem restart",
    )
    parser.add_argument("--timeout", type=float, default=90, help="Teto em segundos (default 90)")
    parser.add_argument(
        "--stable-for",
        type=float,
        default=15,
        dest="stable_for",
        help="Segundos de running contínuo exigidos no modo stable (default 15)",
    )
    parser.add_argument("--project", default="kubo", help="Nome do projeto compose (default: kubo)")
    args = parser.parse_args(argv)

    # Sanitização reconhecível pelo taint-tracking (SonarCloud S8705): além do
    # _validate_names dentro dos waits, o valor que segue adiante é o TEXTO DO
    # MATCH da whitelist de caracteres — argv nunca flui cru para o subprocess.
    service_m = _NAME_RE.fullmatch(args.service)
    project_m = _NAME_RE.fullmatch(args.project)
    if service_m is None or project_m is None:
        bad = args.service if service_m is None else args.project
        print(f"[health-guard] FALHOU: nome inválido: '{bad}'", file=sys.stderr)
        return 1
    args.service = service_m.group(0)
    args.project = project_m.group(0)

    if args.mode == "healthy":
        rc, msg = wait_healthy(args.service, project=args.project, timeout=args.timeout)
    else:
        rc, msg = wait_stable(
            args.service,
            project=args.project,
            timeout=args.timeout,
            stable_for=args.stable_for,
        )
    log_kwargs = {"service": args.service, "mode": args.mode, "project": args.project}
    if rc == 0:
        _log_event("health_guard_ok", "info", **log_kwargs)
    else:
        _log_event("health_guard_failed", "error", **log_kwargs)
    print(msg, file=sys.stderr if rc != 0 else sys.stdout)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
