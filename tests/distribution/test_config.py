"""Pure configuration for the distribution layer (ADR-0027 §11, KUBO-48).

`resolve_base_url` moved from the `destinations.yaml` loader to
`kubo/distribution/config`; these tests followed the function.
"""

from __future__ import annotations

import pytest

from kubo.distribution.config import resolve_base_url
from kubo.errors import ConfigError


def test_resolve_base_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_base_url reads KUBO_BASE_URL from env (strips trailing slash)."""
    monkeypatch.setenv("KUBO_BASE_URL", "https://kubo.test:3900/")
    assert resolve_base_url() == "https://kubo.test:3900"


def test_resolve_base_url_missing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing KUBO_BASE_URL -> ConfigError (digest/report link cannot be broken)."""
    monkeypatch.delenv("KUBO_BASE_URL", raising=False)
    with pytest.raises(ConfigError):
        resolve_base_url()
