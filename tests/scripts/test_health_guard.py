"""Testes do health-guard (incidente CD 2026-07-30).

Provam o comportamento externo sem Docker vivo: o deploy só declara sucesso
quando o container recém-criado prova que está de pé. A regressão nomeada:
kubo-api em crash-loop (ConfigError de env faltante) e o job de CD "success".
Modo `healthy` cobre serviços com healthcheck (kubo-api); modo `stable` cobre
o kubo-scheduler, que não tem healthcheck por decisão do compose.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts import health_guard as hg

_CID = "a1b2c3d4e5f6"  # pragma: allowlist secret (id fake de container, não credencial)


class _FakeRun:
    """Fake de subprocess.run com fila de (stdout, returncode).

    Diferente do fake do digest-guard, REPETE o último item quando a fila
    esvazia — loops de polling consultam o mesmo estado até o timeout.
    """

    def __init__(self, queue: list[tuple[str, int]]) -> None:
        self._queue = list(queue)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self.calls.append(cmd)
        if len(self._queue) > 1:
            stdout, rc = self._queue.pop(0)
        else:
            stdout, rc = self._queue[0]
        return type("Result", (), {"stdout": stdout, "returncode": rc, "stderr": ""})()


class _FakeClock:
    """Relógio determinístico: sleep avança o tempo monotônico."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(hg, "_monotonic", fake.monotonic)
    monkeypatch.setattr(hg, "_sleep", fake.sleep)
    return fake


def _patch_run(monkeypatch: pytest.MonkeyPatch, queue: list[tuple[str, int]]) -> _FakeRun:
    fake = _FakeRun(queue)
    monkeypatch.setattr(hg, "run", fake)
    return fake


# ── modo healthy (kubo-api) ─────────────────────────────────────────────────


def test_healthy_passes_when_container_becomes_healthy(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    _patch_run(
        monkeypatch,
        [
            (f"{_CID}\n", 0),  # docker compose ps -q
            ("running starting", 0),  # ainda no start_period
            ("running healthy", 0),
        ],
    )
    rc, msg = hg.wait_healthy("kubo-api")
    assert rc == 0
    assert "kubo-api" in msg


def test_healthy_fails_fast_on_crash_loop(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    # A regressão do incidente: ConfigError derruba o processo, o container
    # entra em restarting e o healthcheck nunca chega a healthy.
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("restarting starting", 0)])
    rc, msg = hg.wait_healthy("kubo-api")
    assert rc == 1
    assert "restarting" in msg


def test_healthy_fails_fast_on_unhealthy(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("running unhealthy", 0)])
    rc, msg = hg.wait_healthy("kubo-api")
    assert rc == 1
    assert "unhealthy" in msg


def test_healthy_fails_on_timeout_while_still_starting(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("running starting", 0)])
    rc, msg = hg.wait_healthy("kubo-api", timeout=10, interval=3)
    assert rc == 1
    assert "10" in msg
    assert clock.now >= 10


def test_healthy_fails_when_service_has_no_healthcheck(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    # `{{if .State.Health}}` vazio: serviço sem healthcheck não pode fingir saúde.
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("running", 0)])
    rc, msg = hg.wait_healthy("kubo-scheduler")
    assert rc == 1
    assert "healthcheck" in msg


def test_healthy_fails_when_no_container(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    _patch_run(monkeypatch, [("", 0)])
    rc, msg = hg.wait_healthy("kubo-api")
    assert rc == 1
    assert "kubo-api" in msg


def test_healthy_rejects_invalid_service_name(clock: _FakeClock) -> None:
    rc, _msg = hg.wait_healthy("kubo-api; rm -rf /")
    assert rc == 1


# ── modo stable (kubo-scheduler, sem healthcheck) ───────────────────────────


def test_stable_passes_when_running_long_enough(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("running 0", 0)])
    rc, msg = hg.wait_stable("kubo-scheduler", stable_for=15, interval=3)
    assert rc == 0
    assert "kubo-scheduler" in msg
    assert clock.now >= 15


def test_stable_fails_when_restart_count_increments(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    # Crash-loop flagrado mesmo com o container momentaneamente `running`:
    # o force-recreate zera o contador, então qualquer restart é crash.
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("running 2", 0)])
    rc, msg = hg.wait_stable("kubo-scheduler")
    assert rc == 1
    assert "restart" in msg


def test_stable_fails_fast_on_exited(monkeypatch: pytest.MonkeyPatch, clock: _FakeClock) -> None:
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("exited 0", 0)])
    rc, msg = hg.wait_stable("kubo-scheduler")
    assert rc == 1
    assert "exited" in msg


def test_stable_fails_on_timeout_if_never_stable(
    monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    # `created` que nunca vira `running`: não é crash, mas não é sucesso.
    _patch_run(monkeypatch, [(f"{_CID}\n", 0), ("created 0", 0)])
    rc, msg = hg.wait_stable("kubo-scheduler", timeout=10, interval=3)
    assert rc == 1
    assert "10" in msg


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_main_dispatches_healthy_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_wait(service: str, project: str, timeout: float) -> tuple[int, str]:
        seen.update(service=service, project=project, timeout=timeout)
        return 0, "ok"

    monkeypatch.setattr(hg, "wait_healthy", fake_wait)
    rc = hg.main(["kubo-api", "--mode", "healthy", "--timeout", "45"])
    assert rc == 0
    assert seen == {"service": "kubo-api", "project": "kubo", "timeout": 45.0}


def test_main_dispatches_stable_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_wait(service: str, project: str, timeout: float, stable_for: float) -> tuple[int, str]:
        seen.update(service=service, stable_for=stable_for)
        return 1, "falhou"

    monkeypatch.setattr(hg, "wait_stable", fake_wait)
    rc = hg.main(["kubo-scheduler", "--mode", "stable"])
    assert rc == 1
    assert seen == {"service": "kubo-scheduler", "stable_for": 15.0}
