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

from kubo.store.client import DbReader
from kubo.store.tenancy import assert_membership

_TENANT_PARAM = "tenant_id"
_USER_PARAM = "user_id"


class ScopedStore:
    """Sessão de store escopada por tenant.

    Envolve a conexão crua e sobrescreve `query` e `query_raw`, injetando
    `tenant_id`/`user_id` no dict de params antes de delegar. O caller não
    passa esses params; a query os referencia como `$tenant_id`/`$user_id`.

    Membership é checada no construtor (ADR-0053 §1: "uma vez na criação") —
    a checagem mora num sítio só, não em cada factory. O default `superadmin=False`
    é seguro por padrão; o bypass exige `superadmin=True` literal, que é sítio
    de código greppável (ADR-0053 §4), nunca dado derivado de request.

    Não existe `ScopedStore` sem tenant: o construtor exige ambos. Acesso
    genuinamente global usa `PoolReader` ou a conexão crua, com nome próprio.
    """

    def __init__(
        self,
        db: DbReader,
        *,
        tenant_id: RecordID,
        user_id: RecordID,
        superadmin: bool = False,
    ) -> None:
        if not superadmin:
            assert_membership(db, user_id=user_id, tenant_id=tenant_id)
        self._db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.superadmin = superadmin

    def _inject(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """Injeta tenant_id/user_id, sobrescrevendo chaves do caller se houver.

        A sessão é a autoridade: um `tenant_id` vindo do caller nunca pode vencer
        o da sessão — sobrescrever incondicional é a semântica de segurança.
        """
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

    @property
    def db(self) -> DbReader:
        """Conexão crua subjacente: só para leituras globais (tabelas sem tenant_id)."""
        return self._db


class PoolReader:
    """Leitura do pool (tabela `item`, sem `tenant_id` no schema).

    Tipo próprio, distinguível de `ScopedStore` pelo pyright — `Any` deixa
    de ser o shape do bypass na API pública da store.
    """

    def __init__(self, db: DbReader) -> None:
        self._db = db

    def query(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Delega para a conexão crua, sem injeção de tenant."""
        return self._db.query(sql, params)

    def query_raw(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Delega para a conexão crua (raw), sem injeção de tenant."""
        return self._db.query_raw(sql, params)


def scoped(db: DbReader, *, tenant_id: RecordID, user_id: RecordID) -> ScopedStore:
    """Factory de acesso comum: a checagem de membership roda no construtor.

    Membership é checada na mesma conexão da operação. A revogação só passa
    a valer no próximo `ScopedStore` — a sessão vive o tempo de um request.
    """
    return ScopedStore(db, tenant_id=tenant_id, user_id=user_id)


def scoped_superadmin(db: DbReader, *, user_id: RecordID, tenant_id: RecordID) -> ScopedStore:
    """Factory de acesso administrativo: dispensa membership, exige tenant explícito.

    O que o superadmin pula é a linha de `membership`, não o predicado
    `$tenant_id` — operação administrativa acontece *sobre* um tenant,
    escolhido explicitamente. Não existe `ScopedStore` sem tenant.
    """
    return ScopedStore(db, tenant_id=tenant_id, user_id=user_id, superadmin=True)
