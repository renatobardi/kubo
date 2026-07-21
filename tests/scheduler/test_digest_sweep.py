"""Sweep de destinos do digest (ADR-0029) — unit, sem banco real.

Espelha os testes de `execute_sweep_job`: o scheduler itera destinos ativos e
dispara um `run_worker` por destino, isolado. Pausa = zero runs; canal fora do
mapa é ignorado com log.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from surrealdb import RecordID

from kubo.store.destinations import Channel, Destination
from kubo.store.settings import Settings

_EMAIL_PASSWORD = "app-password"  # pragma: allowlist secret


def _destination(key: str, channel: str = "telegram") -> Destination:
    return Destination(
        id=RecordID("destination", key),
        name=f"dest-{key}",
        kind="pessoa",
        channel=cast(Channel, channel),
        address=f"addr-{key}",
        enabled=True,
        archived_at=None,
        dispatches=0,
    )


class _DummyCtx:
    """Context manager mínimo que devolve um db fake."""

    def __enter__(self) -> Any:
        return MagicMock()

    def __exit__(self, *_: object) -> bool:
        return False


def _settings(paused: bool = False) -> Settings:
    return Settings(
        id=RecordID("settings", "global"),
        digest_cron="30 9 * * *",
        distribution_paused=paused,
        default_destination=None,
    )


def test_dispatches_one_run_per_active_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sweep com N destinos ativos dispara N runs, um por destino."""
    from kubo import scheduler

    destinations = [_destination("a"), _destination("b")]
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(scheduler.settings_store, "get_settings", lambda db: _settings())
    monkeypatch.setattr(scheduler.destination_store, "active_destinations", lambda db: destinations)
    monkeypatch.setattr(scheduler, "resolve_base_url", lambda: "https://kubo.test")
    calls: list[tuple[Any, dict[str, Any]]] = []

    def _run_worker(db: Any, worker: Any, *, config: dict[str, Any], embedder: Any) -> None:
        calls.append((worker, config))

    monkeypatch.setattr(scheduler, "run_worker", _run_worker)

    scheduler.execute_digest_sweep_job()

    assert len(calls) == 2
    assert calls[0][0]._destination.id == destinations[0].id
    assert calls[1][0]._destination.id == destinations[1].id
    assert all(c[1] == {"max_items": 50} for c in calls)


def test_zero_runs_when_distribution_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pausado = zero runs; nenhum marcador avança e nenhum run é aberto."""
    from kubo import scheduler

    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(
        scheduler.destination_store, "active_destinations", lambda db: [_destination("x")]
    )
    monkeypatch.setattr(scheduler.settings_store, "get_settings", lambda db: _settings(paused=True))

    def _info(event: str, **kwargs: Any) -> None:
        logged.append({"event": event, **kwargs})

    monkeypatch.setattr(scheduler._log, "info", _info)
    monkeypatch.setattr(
        scheduler,
        "run_worker",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deve rodar")),
    )

    scheduler.execute_digest_sweep_job()

    assert any(e.get("event") == "digest_sweep_skipped" for e in logged)


def test_isolates_failing_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha no run de um destino não aborta os demais."""
    from kubo import scheduler

    destinations = [_destination("ok"), _destination("bad")]
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(scheduler.settings_store, "get_settings", lambda db: _settings())
    monkeypatch.setattr(scheduler.destination_store, "active_destinations", lambda db: destinations)
    monkeypatch.setattr(scheduler, "resolve_base_url", lambda: "https://kubo.test")
    attempted: list[str] = []

    def _run_worker(db: Any, worker: Any, *, config: dict[str, Any], embedder: Any) -> None:
        dest_id = str(worker._destination.id)
        attempted.append(dest_id)
        if "bad" in dest_id:
            raise RuntimeError("envio falhou")

    monkeypatch.setattr(scheduler, "run_worker", _run_worker)

    scheduler.execute_digest_sweep_job()  # NÃO propaga a falha

    assert len(attempted) == 2


def test_unknown_channel_is_ignored_without_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Destino com canal fora de DEST_DISPATCH é logado e skipped; nenhum run aberto."""
    from kubo import scheduler

    destinations = [_destination("tg"), _destination("matrix", channel="matrix")]
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(scheduler.settings_store, "get_settings", lambda db: _settings())
    monkeypatch.setattr(scheduler.destination_store, "active_destinations", lambda db: destinations)
    monkeypatch.setattr(scheduler, "resolve_base_url", lambda: "https://kubo.test")
    calls: list[str] = []
    warnings: list[dict[str, Any]] = []

    def _run_worker(db: Any, worker: Any, *, config: dict[str, Any], embedder: Any) -> None:
        calls.append(str(worker._destination.id))

    def _warning(event: str, **kwargs: Any) -> None:
        warnings.append({"event": event, **kwargs})

    monkeypatch.setattr(scheduler, "run_worker", _run_worker)
    monkeypatch.setattr(scheduler._log, "warning", _warning)

    scheduler.execute_digest_sweep_job()

    assert len(calls) == 1
    assert "matrix" not in calls[0]
    assert any(w.get("event") == "digest_sweep_channel_ignored" for w in warnings)


def test_email_failure_does_not_affect_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha no envio de e-mail não impede o Telegram de entregar no mesmo sweep."""
    from kubo import scheduler

    destinations = [_destination("tg", channel="telegram"), _destination("em", channel="email")]
    monkeypatch.setattr(scheduler.client, "config", lambda: None)
    monkeypatch.setattr(scheduler.client, "connect", lambda _cfg=None: _DummyCtx())
    monkeypatch.setattr(scheduler.settings_store, "get_settings", lambda db: _settings())
    monkeypatch.setattr(scheduler.destination_store, "active_destinations", lambda db: destinations)
    monkeypatch.setattr(scheduler, "resolve_base_url", lambda: "https://kubo.test")
    monkeypatch.setenv("KUBO_EMAIL_HOST", "smtp.example.com")
    monkeypatch.setenv("KUBO_EMAIL_PORT", "587")
    monkeypatch.setenv("KUBO_EMAIL_USER", "kubo@example.com")
    monkeypatch.setenv("KUBO_EMAIL_PASSWORD", _EMAIL_PASSWORD)
    monkeypatch.setenv("KUBO_EMAIL_FROM", "kubo@example.com")
    calls: list[tuple[str, str]] = []

    def _run_worker(db: Any, worker: Any, *, config: dict[str, Any], embedder: Any) -> None:
        calls.append((str(worker._destination.id), worker._destination.channel))
        if worker._destination.channel == "email":
            raise RuntimeError("SMTP fora do ar")

    monkeypatch.setattr(scheduler, "run_worker", _run_worker)

    scheduler.execute_digest_sweep_job()

    assert len(calls) == 2
    channels = [c[1] for c in calls]
    assert "telegram" in channels and "email" in channels
