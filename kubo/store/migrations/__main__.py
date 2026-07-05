"""Runner de deploy: `python -m kubo.store.migrations` aplica migrations pendentes.

Passo explícito de deploy, NÃO roda no boot do scheduler (auto-migrate no boot de
múltiplas réplicas é corrida). Idempotente: `apply_migrations` registra o que já
aplicou, então re-rodar a cada deploy é seguro (no-op quando nada mudou).
"""

from __future__ import annotations

import structlog

from kubo.store import client
from kubo.store.migrations import apply_migrations

_log = structlog.get_logger()


def main() -> list[str]:
    """Conecta por ambiente, aplica migrations pendentes e devolve as recém-aplicadas."""
    with client.connect() as db:
        applied = apply_migrations(db)
    _log.info(
        "migrations applied" if applied else "migrations up to date",
        applied=applied,
    )
    return applied


if __name__ == "__main__":
    main()
