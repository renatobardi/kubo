"""Sessão escopada por tenant (ADR-0053, KUBO-209).

`ScopedStore` envolve a conexão SurrealDB, carrega `(tenant_id, user_id)`,
checa a linha de `membership` uma vez na criação, e injeta `$tenant_id`/
`$user_id` nos params de toda query que emite — incluindo `query_raw`, o
caminho transacional.

O caller referencia `$tenant_id`/`$user_id` no SQL e não passa esses valores.
Esquecer a checagem de membership deixa de ser possível porque não há
parâmetro para omitir.

`PoolReader` é o caminho de leitura do pool (tabela `item`, sem `tenant_id`
no schema) — tipo próprio, distinguível de `ScopedStore` pelo pyright.

Duas factories: `scoped` (checa membership) e `scoped_superadmin` (dispensa
membership, exige tenant explícito). Não é kwarg booleano: a escolha é sítio
de código greppável, não dado alimentável por request.
"""

from __future__ import annotations

from typing import Any

from surrealdb import RecordID

from kubo.store.tenancy import assert_membership

_TENANT_PARAM = "tenant_id"
_USER_PARAM = "user_id"


class ScopedStore:
    """Sessão de store escopada por tenant.

    Envolve a conexão crua e sobrescreve `query` e `query_raw`, injetando
    `tenant_id`/`user_id` no dict de params antes de delegar. O caller não
    passa esses params; a query os referencia como `$tenant_id`/`$user_id`.

    Não existe `ScopedStore` sem tenant: o construtor exige ambos. Acesso
    genuinamente global usa `PoolReader` ou a conexão crua, com nome próprio.
    """

    def __init__(
        self,
        db: Any,
        *,
        tenant_id: RecordID,
        user_id: RecordID,
        superadmin: bool = False,
    ) -> None:
        self._db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.superadmin = superadmin

    def _inject(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """Retorna params com tenant_id/user_id injetados (não sobrescreve chaves do caller)."""
        merged = dict(params) if params else {}
        merged[_TENANT_PARAM] = self.tenant_id
        merged[_USER_PARAM] = self.user_id
        return merged

    def query(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Delega para a conexão crua, injetando $tenant_id/$user_id nos params."""
        return self._db.query(sql, self._inject(params))

    def query_raw(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Delega para a conexão crua (raw), injetando $tenant_id/$user_id nos params.

        É o caminho transacional (`run_transaction` usa `query_raw`) — sem isso,
        toda a escrita escaparia da injeção silenciosamente.
        """
        return self._db.query_raw(sql, self._inject(params))


class PoolReader:
    """Leitura do pool (tabela `item`, sem `tenant_id` no schema).

    Tipo próprio, distinguível de `ScopedStore` pelo pyright — `Any` deixa
    de ser o shape do bypass na API pública da store.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def query(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Delega para a conexão crua, sem injeção de tenant."""
        return self._db.query(sql, params)

    def query_raw(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Delega para a conexão crua (raw), sem injeção de tenant."""
        return self._db.query_raw(sql, params)


def scoped(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> ScopedStore:
    """Factory de acesso comum: checa membership uma vez e devolve a sessão.

    Membership é checada na mesma conexão da operação. A revogação só passa
    a valer no próximo `ScopedStore` — a sessão vive o tempo de um request.
    """
    assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    return ScopedStore(db, tenant_id=tenant_id, user_id=user_id, superadmin=False)


def scoped_superadmin(db: Any, *, user_id: RecordID, tenant_id: RecordID) -> ScopedStore:
    """Factory de acesso administrativo: dispensa membership, exige tenant explícito.

    O que o superadmin pula é a linha de `membership`, não o predicado
    `$tenant_id` — operação administrativa acontece *sobre* um tenant,
    escolhido explicitamente. Não existe `ScopedStore` sem tenant.
    """
    return ScopedStore(db, tenant_id=tenant_id, user_id=user_id, superadmin=True)
