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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from kubo.errors import ConfigError

# Referência de segredo aceita: env:NOME_DA_VAR (maiúsculas/underscore/dígitos).
# Qualquer outra coisa em secret_ref é tratada como valor inline e rejeitada.
_SECRET_REF = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")

AuthType = Literal["none", "bearer", "basic", "api_key"]


class IntegrationAuth(BaseModel):
    """Auth de uma integração — só por referência a env (nunca valor inline)."""

    model_config = ConfigDict(extra="forbid")

    type: AuthType
    secret_ref: str | None = None

    @model_validator(mode="after")
    def _ref_only(self) -> Self:
        """type=none não tem segredo; os demais exigem secret_ref no formato env:VAR."""
        if self.type == "none":
            if self.secret_ref is not None:
                raise ValueError("auth.type=none não deve declarar secret_ref")
            return self
        if self.secret_ref is None:
            raise ValueError(f"auth.type={self.type} exige secret_ref (referência env:VAR)")
        if not _SECRET_REF.match(self.secret_ref):
            raise ValueError(
                "secret_ref deve ser referência env:VAR, nunca valor inline "
                f"(recebi {self.secret_ref!r})"
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
    secret: str | None
    rate_limit: RateLimit | None
    base_url: str | None


def load_integration(path: Path) -> Integration:
    """Carrega e valida um YAML de integração; erro vira ConfigError (fronteira)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"integração {path.name}: YAML não é um mapping")
    try:
        return Integration.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"integração {path.name} inválida: {exc}") from exc


def load_integrations(catalog_dir: Path) -> dict[str, Integration]:
    """Carrega todas as integrações de um diretório (1 YAML por item), por nome."""
    catalog: dict[str, Integration] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        integ = load_integration(path)
        catalog[integ.name] = integ
    return catalog


def _resolve_secret(auth: IntegrationAuth) -> str | None:
    """Resolve a referência env:VAR para o valor concreto; falha alto se ausente."""
    if auth.secret_ref is None:
        return None
    var = auth.secret_ref.removeprefix("env:")
    value = os.environ.get(var)
    if value is None:
        raise ConfigError(f"variável de ambiente {var} (secret_ref) não está definida")
    return value


def resolve_integrations(
    declared: list[str],
    catalog: dict[str, Integration],
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
            secret=_resolve_secret(integ.auth),
            rate_limit=integ.rate_limit,
            base_url=integ.base_url,
        )
    return resolved
