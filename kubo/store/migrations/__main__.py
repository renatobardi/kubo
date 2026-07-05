"""Runner de deploy: `python -m kubo.store.migrations` aplica migrations pendentes.

Passo explícito de deploy, NÃO roda no boot do scheduler (auto-migrate no boot de
múltiplas réplicas é corrida). Idempotente: `apply_migrations` registra o que já
aplicou, então re-rodar a cada deploy é seguro (no-op quando nada mudou).
"""

from __future__ import annotations

import structlog

from kubo.store import client
from kubo.store.migrations import apply_migrations

# `worker` no contexto (convenção de log do CLAUDE.md); flow_id/task_id não se
# aplicam a um runner de deploy one-shot (não há flow/task).
_log = structlog.get_logger().bind(worker="migrations-cli")


def main() -> list[str]:
    """Conecta por ambiente, aplica migrations pendentes e devolve as recém-aplicadas."""
    try:
        with client.connect() as db:
            applied = apply_migrations(db)
    except Exception:  # noqa: BLE001 — loga estruturado e repropaga; não engole o erro
        # Sem o log, uma falha de auth/query sairia só como traceback cru. O re-raise
        # preserva o exit code não-zero que sinaliza a falha ao passo de deploy.
        _log.exception("migrations_failed")
        raise
    _log.info(
        "migrations applied" if applied else "migrations up to date",
        applied=applied,
    )
    return applied


if __name__ == "__main__":
    main()
