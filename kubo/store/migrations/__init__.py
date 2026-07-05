"""Runner mínimo de migrations do SurrealDB — DDL versionada.

Arquivos `.surql` numerados vivem neste pacote, aplicados em ordem de nome. A
tabela `migration` registra o que já rodou; reexecução é no-op. Sem
down-migrations (spike): rollback é restaurar backup, não desfazer no schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).parent


def _applied(db: Any) -> set[str]:
    """Nomes de migrations já registrados na tabela `migration`."""
    rows: list[dict[str, Any]] = db.query("SELECT name FROM migration;") or []
    return {str(row["name"]) for row in rows}


def apply_migrations(db: Any, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Aplica migrations pendentes em ordem de nome; retorna as recém-aplicadas.

    Cada `.surql` e o registro na tabela `migration` rodam numa ÚNICA transação:
    ou os dois entram, ou nenhum — o runner não deixa registro órfão nem migration
    aplicada sem registro. `migrations_dir` é parâmetro só para testes; produção
    usa o default (o próprio pacote).

    Convenções de autoria dos `.surql`:
    - Sem controle de transação próprio (`BEGIN`/`COMMIT`) — o runner envolve.
    - DDL idempotente (`DEFINE ... OVERWRITE` / `IF NOT EXISTS`).
    Sem down-migrations: rollback é restaurar backup, não desfazer no schema.
    """
    db.query("DEFINE TABLE IF NOT EXISTS migration SCHEMALESS;")
    # Índice UNIQUE: se dois runners concorrentes (ex.: réplicas subindo juntas)
    # virem a mesma migration pendente, o 2º CREATE falha no índice e sua
    # transação reverte — em vez de duplicar registro ou reaplicar DDL.
    db.query("DEFINE INDEX IF NOT EXISTS migration_name ON migration FIELDS name UNIQUE;")
    already = _applied(db)
    newly: list[str] = []
    for path in sorted(migrations_dir.glob("*.surql")):
        if path.name in already:
            continue
        # Conteúdo do `.surql` é confiável (arquivo versionado do repo, não entrada
        # coletada). O único valor variável (nome) vai por bind param.
        transaction = (
            f"BEGIN;\n{path.read_text(encoding='utf-8')}\n"
            "CREATE migration SET name = $name, applied_at = time::now();\nCOMMIT;"
        )
        db.query(transaction, {"name": path.name})  # noqa: S608 (surql confiável)
        newly.append(path.name)
    return newly
