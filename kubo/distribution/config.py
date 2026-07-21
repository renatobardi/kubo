"""Pure configuration for the distribution layer (ADR-0027 §11, KUBO-48).

`resolve_base_url` used to live in the `destinations.yaml` loader; with the cutover
it lives here, decoupled from destination management. `KUBO_BASE_URL` is the base
for digest/report links in the UI (invariant 8: env-only, never in file/code).
"""

from __future__ import annotations

import os

from kubo.errors import ConfigError

_BASE_URL_VAR = "KUBO_BASE_URL"


def resolve_base_url() -> str:
    """Resolve `KUBO_BASE_URL` (link base for digest/report UI), without trailing slash.

    Missing → ConfigError: a broken link is worse than failing early.
    """
    value = os.environ.get(_BASE_URL_VAR)
    if not value:
        raise ConfigError(f"missing environment variable {_BASE_URL_VAR}")
    return value.rstrip("/")
