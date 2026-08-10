"""Wrapper transacional da store — escrita multi-statement atômica e VERIFICADA.

Contrato do ADR-0005 (spike SurrealDB v3.1.5): um erro no meio de uma transação
reverte tudo, mas NÃO propaga via `query()` — que só inspeciona o 1º statement.
Confiar em exceção deixaria passar uma falha silenciosa. Este wrapper usa
`query_raw` e checa o `status` de TODOS os statements; se qualquer um falhou,
levanta `StoreError` (a transação já reverteu no servidor).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kubo.errors import StoreError

if TYPE_CHECKING:
    from kubo.store.client import DbConnection, UnscopedDb
    from kubo.store.scoped import ScopedStore


def _execute_transaction(db: UnscopedDb, statements: list[str], params: dict[str, Any]) -> None:
    """Envia statements em BEGIN/COMMIT por `query_raw` e falha alta se algo ERR."""
    body = ";\n".join(s.strip().rstrip(";") for s in statements)
    surql = f"BEGIN;\n{body};\nCOMMIT;"  # noqa: S608 (surql montado só de literais + bind params)
    raw = db.query_raw(surql, params)
    failed = [r for r in raw["result"] if r.get("status") == "ERR"]
    if failed:
        detail = "; ".join(str(r.get("result")) for r in failed)
        raise StoreError(f"transação revertida: {detail}")


def run_transaction(
    session: ScopedStore,
    statements: list[str],
    params: dict[str, Any] | None = None,
) -> None:
    """Roda os statements numa única transação atômica; levanta se algum falhar.

    Os statements são envolvidos em `BEGIN;…;COMMIT;` e enviados por `query_raw`.
    Conteúdo variável entra SEMPRE por `params` (bind param), inclusive dentro da
    string transacional — nunca interpolado (entrada coletada é hostil, ADR-0005).
    Os `params` são compartilhados por todos os statements da transação; o chamador
    garante chaves únicas quando um statement se repete (ex.: `$chunk_0_text`).

    Não retorna resultados: a store deriva IDs de forma determinística, não os lê
    da resposta. O valor deste wrapper é a garantia de atomicidade + a falha alta.
    """
    _execute_transaction(session, statements, params or {})


def run_global_transaction(
    db: DbConnection,
    statements: list[str],
    params: dict[str, Any] | None = None,
) -> None:
    """Roda statements numa transação atômica SEM injeção de tenant (tabelas globais).

    Usado pela maquinaria de tenancy/invites/migrations — acesso global legítimo
    que não carrega escopo de tenant (ADR-0053 §5)."""
    _execute_transaction(db, statements, params or {})
