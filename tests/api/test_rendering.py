"""Filtros de apresentação de datetime — storage é UTC, tela é tz local (fix tz).

Regra permanente: TODO datetime formatado para humano converte pra tz local
(America/Sao_Paulo por default, override via env TZ); armazenar/comparar fica UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kubo.api import rendering


def test_short_datetime_converts_utc_aware_to_sao_paulo() -> None:
    # 12:00Z → São Paulo (UTC-3, sem horário de verão desde 2019) = 09:00.
    assert rendering.short_datetime("2026-07-14T12:00:00+00:00") == "Jul 14, 09:00"


def test_short_datetime_assumes_utc_for_naive_input() -> None:
    # A store devolve str(datetime) UTC; naive é tratado como UTC, não como local.
    assert rendering.short_datetime("2026-07-14T12:00:00") == "Jul 14, 09:00"


def test_short_datetime_respects_env_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    assert rendering.short_datetime("2026-07-14T12:00:00+00:00") == "Jul 14, 12:00"


def test_short_datetime_empty_env_tz_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # TZ="" (comum em Docker/K8s) não pode explodir ZoneInfoNotFoundError → usa o default.
    monkeypatch.setenv("TZ", "")
    assert rendering.short_datetime("2026-07-14T12:00:00+00:00") == "Jul 14, 09:00"


def test_short_datetime_missing_is_dash() -> None:
    assert rendering.short_datetime(None) == "—"


def test_days_since_counts_in_local_tz() -> None:
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3, hours=1)
    assert rendering.days_since(three_days_ago.isoformat()) == 3


def test_duration_is_tz_invariant_and_handles_naive() -> None:
    # Delta não muda com tz; naive + aware não pode explodir.
    assert rendering.duration("2026-07-14T12:00:00", "2026-07-14T12:00:48+00:00") == "48s"
