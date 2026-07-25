#!/usr/bin/env python3
"""digest_guard.py — compara o digest esperado com o digest da imagem viva.

Uso:
    python -m scripts.digest_guard <expected-digest> <service-name> [--project <project>]
    python scripts/digest_guard.py <expected-digest> <service-name> [--project <project>]

O digest pode vir com ou sem o prefixo ``sha256:``. O script inspeciona o
container em execução do serviço no compose, obtém o digest da imagem
(RepoDigests, com fallback para Config.Image) e compara com o digest esperado.
Sai com código 0 quando coincidem e 1 quando divergem, container ausente ou
imagem sem digest determinístico.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

_HEX_RE = re.compile(r"^[a-f0-9]+$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def normalize_digest(value: str) -> str:
    """Remove o prefixo 'sha256:' e força minúsculas."""
    return value.removeprefix("sha256:").lower()


def extract_digest(image_reference: str) -> str:
    """Extrai o digest sha256 de uma referência de imagem.

    Aceita formatos como ``ghcr.io/owner/repo@sha256:abc`` ou ``repo@sha256:abc``.
    """
    if "@sha256:" in image_reference:
        return image_reference.split("@sha256:", 1)[1].lower()
    return image_reference.lower()


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Wrapper fino em torno de subprocess.run para facilitar testes."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)  # noqa: S603


def _container_id(service: str, project: str) -> str:
    """Retorna o id do container em execução do serviço, ou levanta RuntimeError."""
    if not _NAME_RE.fullmatch(service):
        raise RuntimeError(f"nome de serviço inválido: '{service}'")
    if not _NAME_RE.fullmatch(project):
        raise RuntimeError(f"nome de projeto inválido: '{project}'")
    result = run(
        ["docker", "compose", "--project-name", project, "ps", "-q", service],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose ps falhou para '{service}': {result.stderr.strip()}")
    container_id = result.stdout.strip().split("\n")[0]
    if not container_id:
        raise RuntimeError(f"Nenhum container em execução para o serviço '{service}'")
    if not _HEX_RE.fullmatch(container_id):
        raise RuntimeError(f"id de container inválido: '{container_id}'")
    return container_id


def _running_digest(container_id: str) -> str:
    """Obtém o digest (sha256) da imagem do container."""
    if not _HEX_RE.fullmatch(container_id):
        raise RuntimeError(f"id de container inválido: '{container_id}'")

    repo_digest = run(
        ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", container_id],
        check=False,
    ).stdout.strip()
    if repo_digest and repo_digest != "<no value>":
        return extract_digest(repo_digest)

    config_image = run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container_id],
        check=False,
    ).stdout.strip()
    if config_image and "@sha256:" in config_image:
        return extract_digest(config_image)

    raise RuntimeError("Não foi possível determinar o digest da imagem do container")


def guard(expected: str, service: str, project: str = "kubo") -> tuple[int, str]:
    """Compara ``expected`` com o digest vivo de ``service``.

    Retorna ``(0, mensagem)`` quando coincidem e ``(1, mensagem)`` quando
    divergem ou não consegue inspecionar.
    """
    if not _NAME_RE.fullmatch(service):
        return 1, f"[digest-guard] FALHOU: nome de serviço inválido: '{service}'"
    if not _NAME_RE.fullmatch(project):
        return 1, f"[digest-guard] FALHOU: nome de projeto inválido: '{project}'"

    expected_digest = normalize_digest(expected)
    if not _HEX_RE.fullmatch(expected_digest):
        return 1, f"[digest-guard] FALHOU: digest '{expected}' não é hexadecimal"

    try:
        container_id = _container_id(service, project)
        actual_digest = _running_digest(container_id)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        return 1, f"[digest-guard] FALHOU: {exc}"

    if actual_digest != expected_digest:
        return (
            1,
            f"[digest-guard] FALHOU: digest diverge para '{service}' "
            f"(esperado sha256:{expected_digest}, vivo sha256:{actual_digest})",
        )
    return 0, f"[digest-guard] OK: {service} roda sha256:{actual_digest}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compara digest esperado com digest da imagem viva de um serviço compose."
    )
    parser.add_argument("expected_digest", help="Digest esperado (com ou sem 'sha256:')")
    parser.add_argument("service", help="Nome do serviço no docker compose")
    parser.add_argument("--project", default="kubo", help="Nome do projeto compose (default: kubo)")
    args = parser.parse_args(argv)

    rc, msg = guard(args.expected_digest, args.service, args.project)
    print(msg, file=sys.stderr if rc != 0 else sys.stdout)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
