"""Testes do digest-guard (KUBO-98).

Provam o comportamento externo do script sem depender do Docker vivo:
digest coincidente -> sucesso; divergente, container ausente ou imagem sem
digest conhecido -> falha fechada. A regressão nomeada e o caso que o
guard antigo (KUBO_BUILD_ID) deixava passar: container rodando imagem de
conteúdo diferente do promovido.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts import digest_guard as dg


class _FakeRun:
    """Fake de subprocess.run com fila de (stdout, returncode)."""

    def __init__(self, queue: list[tuple[str, int]]) -> None:
        self._queue = list(queue)
        self._calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self._calls.append(cmd)
        if not self._queue:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="no more mocks")
        stdout, rc = self._queue.pop(0)
        result = type("Result", (), {"stdout": stdout, "returncode": rc, "stderr": ""})()
        check = kwargs.get("check", True)
        if rc != 0 and check:
            exc = subprocess.CalledProcessError(rc, cmd, output=stdout, stderr="")
            exc.stdout = stdout  # type: ignore[attr-defined]
            raise exc
        return result


def test_normalize_digest_strips_sha256_prefix_and_lowercases() -> None:
    assert dg.normalize_digest("sha256:ABC123def456") == "abc123def456"  # pragma: allowlist secret


def test_normalize_digest_accepts_plain_hex() -> None:
    assert dg.normalize_digest("aBc123") == "abc123"


def test_extract_digest_from_repo_digest() -> None:
    assert dg.extract_digest("ghcr.io/renatobardi/kubo@sha256:abc123") == "abc123"


def test_extract_digest_from_config_image_reference() -> None:
    assert dg.extract_digest("kubo@sha256:abc123") == "abc123"


def test_guard_passes_when_digests_match(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(
        [
            ("a1b2c3d4e5f6\n", 0),  # docker compose ps -q
            ("ghcr.io/renatobardi/kubo@sha256:abc123\n", 0),  # repo digest
        ]
    )
    monkeypatch.setattr(dg, "run", fake)

    rc, msg = dg.guard("sha256:abc123", "kubo-api")

    assert rc == 0
    assert "OK" in msg
    assert "abc123" in msg


def test_guard_fails_when_digests_diverge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regressão: imagem viva com conteúdo diferente do promovido deve falhar."""
    fake = _FakeRun(
        [
            ("a1b2c3d4e5f6\n", 0),
            ("ghcr.io/renatobardi/kubo@sha256:deadbeef0101\n", 0),
        ]
    )
    monkeypatch.setattr(dg, "run", fake)

    rc, msg = dg.guard("sha256:abc123", "kubo-api")

    assert rc == 1
    assert "diverge" in msg


def test_guard_fails_when_container_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun([("\n", 1)])
    monkeypatch.setattr(dg, "run", fake)

    rc, msg = dg.guard("sha256:abc123", "kubo-api")

    assert rc == 1


def test_guard_fails_when_container_id_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun([("\n", 0)])
    monkeypatch.setattr(dg, "run", fake)

    rc, msg = dg.guard("sha256:abc123", "kubo-api")

    assert rc == 1
    assert "Nenhum container" in msg


def test_guard_fails_when_image_has_no_repo_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(
        [
            ("a1b2c3d4e5f6\n", 0),
            ("<no value>\n", 0),  # RepoDigests vazio
            ("kubo:latest\n", 0),  # Config.Image sem digest
        ]
    )
    monkeypatch.setattr(dg, "run", fake)

    rc, msg = dg.guard("sha256:abc123", "kubo-api")

    assert rc == 1
    assert "digest" in msg


def test_guard_falls_back_to_config_image_when_repo_digest_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRun(
        [
            ("a1b2c3d4e5f6\n", 0),
            ("<no value>\n", 1),  # RepoDigests inexistente
            ("ghcr.io/renatobardi/kubo@sha256:abc123\n", 0),  # Config.Image com digest
        ]
    )
    monkeypatch.setattr(dg, "run", fake)

    rc, msg = dg.guard("sha256:abc123", "kubo-api")

    assert rc == 0
    assert "abc123" in msg


def test_main_cli_prints_and_returns_zero_on_match(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(
        [
            ("a1b2c3d4e5f6\n", 0),
            ("ghcr.io/renatobardi/kubo@sha256:abc123\n", 0),
        ]
    )
    monkeypatch.setattr(dg, "run", fake)

    rc = dg.main(["sha256:abc123", "kubo-api"])

    assert rc == 0
