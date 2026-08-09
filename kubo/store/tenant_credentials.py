"""Credenciais de provedor cifradas por tenant (ADR-0039 §IV, KUBO-115).

Cada tenant guarda N chaves (uma por `provider`). O segredo em si nunca é
persistido em claro: a camada de store cifra com Fernet antes de escrever e
decifra após ler. A chave mestra vem de `KUBO_TENANT_CREDENTIAL_KEY`.

Toda operação pública recebe uma `ScopedStore` (KUBO-212, ADR-0053): a sessão
carrega `(tenant_id, user_id)`, checa membership na criação, e injeta
`$tenant_id`/`$user_id` nos params de toda query. O caller não passa
tenant_id/user_id — a sessão os fornece.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet
from surrealdb import RecordID

from kubo.errors import ConfigError, StoreError
from kubo.store.scoped import ScopedStore


def _fernet() -> Fernet:
    """Carrega a chave mestra do env; falha rápido se ausente."""
    raw = os.environ.get("KUBO_TENANT_CREDENTIAL_KEY")
    if not raw:
        raise ConfigError(
            "KUBO_TENANT_CREDENTIAL_KEY ausente — cifragem de credenciais de tenant indisponível"
        )
    return Fernet(raw)


def _encrypt(secret: str) -> str:
    """Cifra um segredo; devolve string ASCII segura para o SurrealDB."""
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def _decrypt(token: str) -> str:
    """Decifra um segredo previamente cifrado."""
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def _record_id(tenant_id: RecordID, provider: str) -> RecordID:
    """Id determinístico para (tenant, provider): provider é parte da chave."""
    return RecordID("tenant_credential", f"{tenant_id.id}:{provider.strip()}")


def set_credential(
    session: ScopedStore,
    *,
    provider: str,
    secret: str,
) -> None:
    """Cria ou atualiza a credencial cifrada de um provider para um tenant."""
    if not provider.strip():
        raise StoreError("provider cannot be empty")
    if not secret:
        raise StoreError("secret cannot be empty")

    encrypted = _encrypt(secret)
    session.query(
        "UPSERT $id SET tenant_id = $tenant_id, provider = $provider, "
        "`value` = $value, updated_at = time::now();",
        {
            "id": _record_id(session.tenant_id, provider),
            "provider": provider.strip(),
            "value": encrypted,
        },
    )


def get_credential(
    session: ScopedStore,
    *,
    provider: str,
) -> str | None:
    """Lê e decifra a credencial de um provider para um tenant, ou None."""
    rows = session.query(
        "SELECT `value` FROM tenant_credential WHERE id = $id AND tenant_id = $tenant_id LIMIT 1;",
        {"id": _record_id(session.tenant_id, provider)},
    )
    if not rows:
        return None
    return _decrypt(rows[0]["value"])


def delete_credential(
    session: ScopedStore,
    *,
    provider: str,
) -> None:
    """Revoga (apaga) a credencial de um provider para um tenant."""
    session.query(
        "DELETE $id;",
        {"id": _record_id(session.tenant_id, provider)},
    )
