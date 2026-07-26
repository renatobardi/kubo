"""Loader do catálogo `integrations` + resolução de segredo + negação (ADR-0009).

O catálogo é declarativo (invariante 3): 1 YAML por integração, sem lógica.
Auth é só por REFERÊNCIA (`env:VAR`) — valor inline é rejeitado no schema
(invariante 8). A resolução do segredo é do RUNTIME, na montagem do ctx: o
worker nunca lê `os.environ`, e o valor resolvido vive só no objeto do ctx,
nunca em log. É aqui que o least-privilege é enforçado — só as integrações
declaradas ∩ existentes são injetadas; o resto é negado (plano 0004 §4.3.3).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from kubo.errors import ConfigError
from kubo.runtime.catalog_loader import (
    load_items_from_db,
    load_items_from_dir,
    load_yaml_item,
)
from kubo.store import tenant_credentials

_KIND = "integração"

# Referência de segredo aceita:
# - env:NOME_DA_VAR (maiúsculas/underscore/dígitos) — segredo de sistema.
# - tenant_credential:<nome> — chave cifrada do tenant (ADR-0039 §IV, KUBO-115).
# Qualquer outra coisa em secret_ref é tratada como valor inline e rejeitada.
_SECRET_REF = re.compile(r"^(env:[A-Z_][A-Z0-9_]*|tenant_credential:[A-Za-z0-9_.:-]+)$")

AuthType = Literal["none", "bearer", "basic", "api_key"]


class IntegrationAuth(BaseModel):
    """Auth de uma integração — só por referência a env (nunca valor inline)."""

    model_config = ConfigDict(extra="forbid")

    type: AuthType
    secret_ref: str | None = None

    @model_validator(mode="after")
    def _ref_only(self) -> Self:
        """type=none não tem segredo; os demais exigem secret_ref como
        env:VAR ou tenant_credential:<nome>."""
        if self.type == "none":
            if self.secret_ref is not None:
                raise ValueError("auth.type=none não deve declarar secret_ref")
            return self
        if self.secret_ref is None:
            raise ValueError(
                f"auth.type={self.type} exige secret_ref (env:VAR ou tenant_credential:<nome>)"
            )
        if not _SECRET_REF.match(self.secret_ref):
            # NÃO ecoa o valor: se alguém colou um token real por engano, a mensagem
            # não pode vazá-lo para o ConfigError/run.error. Só diz a regra violada.
            raise ValueError(
                "secret_ref deve ser referência (env:VAR ou tenant_credential:<nome>), "
                "nunca valor inline"
            )
        return self


class RateLimit(BaseModel):
    """Limite de taxa declarativo — dado, não lógica (invariante 3)."""

    model_config = ConfigDict(extra="forbid")

    requests_per_minute: int


class Integration(BaseModel):
    """Uma integração do catálogo (1 YAML por arquivo)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    auth: IntegrationAuth
    rate_limit: RateLimit | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class ResolvedIntegration:
    """Integração com o segredo já resolvido pelo runtime.

    `secret` é o valor concreto (ou None para auth pública) e NUNCA deve ser
    logado — o ctx é read-only e o logger nunca carrega este objeto.
    """

    name: str
    kind: str
    auth_type: str
    # repr=False: o segredo NUNCA aparece em repr/str/traceback. Fecha por
    # construção o canal em que um worker faz `raise RuntimeError(ctx.integrations[x])`
    # e o valor cairia em run.error via str(exc). Não é disciplina, é tipo.
    secret: str | None = field(repr=False)
    rate_limit: RateLimit | None
    base_url: str | None


def load_integration(path: Path) -> Integration:
    """Carrega e valida um YAML de integração; erro vira ConfigError (fronteira)."""
    return load_yaml_item(path, Integration, _KIND)


def load_integrations_from_dir(catalog_dir: Path) -> dict[str, Integration]:
    """Carrega todas as integrações de um diretório (1 YAML por item), por nome.

    Nome duplicado entre dois arquivos falha alto (ConfigError) — nunca sobrescreve
    em silêncio: least-privilege depende de o catálogo ser inequívoco (um nome, uma
    integração, uma permissão), senão um worker poderia herdar a auth/base_url errada."""
    return load_items_from_dir(catalog_dir, Integration, _KIND)


def load_integrations(db: Any, tenant_id: Any, user_id: Any) -> dict[str, Integration]:
    """Carrega as integrações do catálogo do tenant no banco (ADR-0042).

    Leitura direta a cada chamada, sem cache."""
    from kubo.store import catalog as _catalog_store

    return load_items_from_db(
        db,
        tenant_id,
        user_id,
        _catalog_store.list_integrations,
        Integration,
        _KIND,
    )


def _resolve_secret(
    auth: IntegrationAuth,
    *,
    db: Any = None,
    tenant_id: Any = None,
) -> str | None:
    """Resolve a referência de segredo para o valor concreto; falha alto se ausente.

    `env:VAR` resolve contra ambiente. `tenant_credential:<nome>` resolve contra
    a tabela `tenant_credential` do tenant ativo (KUBO-115) — requer `db` e
    `tenant_id`. Qualquer outro formato já foi rejeitado pelo schema.
    """
    if auth.secret_ref is None:
        return None
    if auth.secret_ref.startswith("env:"):
        var = auth.secret_ref.removeprefix("env:")
        value = os.environ.get(var)
        if value is None:
            raise ConfigError(f"variável de ambiente {var} (secret_ref) não está definida")
        return value
    if auth.secret_ref.startswith("tenant_credential:"):
        if db is None or tenant_id is None:
            raise ConfigError("secret_ref tenant_credential requer contexto de tenant ativo")
        name = auth.secret_ref.removeprefix("tenant_credential:")
        # Runtime sem user_id: membership já foi checada na borda da requisição.
        secret = tenant_credentials.get_credential(
            db,
            tenant_id=tenant_id,
            provider=name,
            user_id=None,
        )
        if secret is None:
            raise ConfigError(f"credencial de tenant '{name}' não encontrada para o tenant ativo")
        return secret
    return None


def resolve_integrations(
    declared: list[str],
    catalog: dict[str, Integration],
    *,
    db: Any = None,
    tenant_id: Any = None,
) -> dict[str, ResolvedIntegration]:
    """Injeta só as integrações DECLARADAS ∩ existentes; nega o resto.

    Negação acontece AQUI (montagem do ctx), não na validação do manifest:
    manifest válido ≠ permissão concedida. Uma integração declarada que não
    existe no catálogo falha alto (least-privilege, plano §4.3.3). O segredo é
    resolvido pelo runtime — o worker nunca lê `os.environ`.
    """
    resolved: dict[str, ResolvedIntegration] = {}
    for name in declared:
        integ = catalog.get(name)
        if integ is None:
            raise ConfigError(f"integração declarada '{name}' não existe no catálogo")
        resolved[name] = ResolvedIntegration(
            name=integ.name,
            kind=integ.kind,
            auth_type=integ.auth.type,
            secret=_resolve_secret(integ.auth, db=db, tenant_id=tenant_id),
            rate_limit=integ.rate_limit,
            base_url=integ.base_url,
        )
    return resolved
