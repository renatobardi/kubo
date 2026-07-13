"""Loader de `destinations.yaml` (o "para-quem" da distribuição, ADR-0015 §I).

Terceiro eixo declarativo do ateliê — catálogos = o quê, `schedules.yaml` =
quando, `destinations.yaml` = para quem — mora na raiz, ao lado dos outros. NÃO
é um 4º catálogo (não descreve artefato); é o par do schedules. Endereço só por
REFERÊNCIA a env (`env:VAR`, mesma máquina do `secret_ref` das integrações):
chat_id / e-mail é PII, fica fora do repo por construção (invariante 8). O
endereço RESOLVIDO vive em `field(repr=False)` — nunca em repr/traceback.

`KUBO_BASE_URL` (link do digest para a UI) é resolvido pelo mesmo módulo: o
worker não lê `os.environ`, recebe destinos resolvidos + base URL por injeção.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from kubo.errors import ConfigError, format_validation_error

# Referência de endereço aceita: env:NOME_DA_VAR — idêntico ao secret_ref das
# integrações (kubo/runtime/integrations.py). Qualquer outra coisa é valor inline
# e é rejeitada na borda (PII nunca no repo).
_ENV_REF = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")
_BASE_URL_VAR = "KUBO_BASE_URL"

# Canais de entrega (D11: pessoa|sistema entregam por um destes). Literal fechado —
# casa 1:1 com `DispatchPayload.channel`, então o tipo atravessa da config ao fato.
Channel = Literal["telegram", "email"]


class Destination(BaseModel):
    """Um destino declarado (1 entrada do destinations.yaml). D11: pessoa|sistema."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: Literal["pessoa", "sistema"]
    channel: Channel
    address_ref: str

    @field_validator("address_ref")
    @classmethod
    def _ref_only(cls, v: str) -> str:
        """address_ref é só referência env:VAR; valor inline é rejeitado SEM ecoá-lo
        (PII colada por engano não pode vazar para o ConfigError/log)."""
        if not _ENV_REF.match(v):
            raise ValueError(
                "address_ref deve ser referência no formato env:VAR, nunca valor inline"
            )
        return v


class _DestinationsFile(BaseModel):
    """Envelope do arquivo: lista de destinos, `extra="forbid"` na borda."""

    model_config = ConfigDict(extra="forbid")

    destinations: list[Destination]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        """`id` é a chave do watermark em `dispatch.destination` (string): dois destinos
        com o mesmo id compartilhariam o watermark — um `ok` de um avançaria o do outro
        em silêncio. Duplicata falha na borda."""
        ids = [d.id for d in self.destinations]
        if len(set(ids)) != len(ids):
            raise ValueError("destinos com `id` duplicado — cada destino deve ter id único")
        return self


@dataclass(frozen=True)
class ResolvedDestination:
    """Destino com o endereço já resolvido do env. `address` (PII: chat_id/e-mail)
    fica em `field(repr=False)` — o mesmo fechamento por tipo do
    `ResolvedIntegration.secret`: nunca cai em repr/str/traceback."""

    id: str
    name: str
    kind: str
    channel: Channel
    address: str = field(repr=False)


def load_destinations(path: Path) -> list[Destination]:
    """Carrega e valida o destinations.yaml; erro vira ConfigError (fronteira)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"destinations {path.name}: YAML não é um mapping")
    try:
        return _DestinationsFile.model_validate(raw).destinations
    except ValidationError as exc:
        raise ConfigError(f"destinations inválido: {format_validation_error(exc)}") from exc


def resolve_destinations(destinations: list[Destination]) -> list[ResolvedDestination]:
    """Resolve o `address_ref` de cada destino a partir do env. Env ausente falha
    alto (ConfigError) — nunca sobe um destino meio-resolvido."""
    return [_resolve_one(d) for d in destinations]


def _resolve_one(dest: Destination) -> ResolvedDestination:
    """Resolve um destino; env referenciada ausente/vazia → ConfigError."""
    var = dest.address_ref.removeprefix("env:")
    value = os.environ.get(var)
    if not value:
        raise ConfigError(f"destino {dest.id!r}: variável de ambiente {var} ausente")
    return ResolvedDestination(
        id=dest.id, name=dest.name, kind=dest.kind, channel=dest.channel, address=value
    )


def resolve_base_url() -> str:
    """Resolve `KUBO_BASE_URL` (base do link do digest para a UI), sem barra final.
    Ausente → ConfigError: um link quebrado no digest é pior que falhar cedo."""
    value = os.environ.get(_BASE_URL_VAR)
    if not value:
        raise ConfigError(f"variável de ambiente {_BASE_URL_VAR} ausente")
    return value.rstrip("/")
