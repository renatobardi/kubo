"""Modelos pydantic do contrato de worker (ADR-0009).

Define o manifest do worker, os payloads que ele devolve (união discriminada
que espelha 1:1 as assinaturas de escrita de `kubo/store/knowledge.py`) e o
envelope `RunResult` que o runtime persiste — o worker nunca fala com a store
diretamente.

Postura de segurança (ADR-0009 itens I/IV/VIII): todos os modelos do contrato
são `extra="forbid"` — um campo com nome errado é rejeitado, não descartado em
silêncio (a "validação antes de persistir", regra 2 de D6, seria meia-verdade
com o default `ignore` do pydantic). A exceção é `Stats`, `extra="allow"` por
design, mas com um validador que rejeita valor extra não-numérico.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkerManifest(BaseModel):
    """Identidade e config declarada de um worker (ADR-0009 item II).

    `config` é o schema de config como CLASSE pydantic (`type[BaseModel]`),
    não um dict JSON-schema: o runtime valida a config concreta instanciando
    a classe (`manifest.config.model_validate(data)`).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    schema_version: Literal[1] = 1
    integrations: list[str] = Field(default_factory=list)
    config: type[BaseModel]


class SourcePayload(BaseModel):
    """Espelha `upsert_source(kind=..., canonical=..., title=...)`."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["source"] = "source"
    kind: str
    canonical: str
    title: str | None = None


class ItemPayload(BaseModel):
    """Espelha `upsert_item(source=..., external_id=..., content=...)`.

    A `SourcePayload` vai embutida inline: o runner faz upsert da source
    antes do item, e como o upsert é idempotente por chave natural, repetir
    a source em cada item é gratuito (ADR-0009 item III).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["item"] = "item"
    source: SourcePayload
    external_id: str
    content: str
    url: str | None = None
    title: str | None = None
    metadata: dict[str, Any] | None = None


Payload: TypeAlias = Annotated[SourcePayload | ItemPayload, Field(discriminator="type")]


class Stats(BaseModel):
    """Contadores livres do worker — envelope tipado sobre `run.stats`.

    Permissivo nos NOMES (cada worker conta métricas próprias), mas rejeita
    valor extra que não seja numérico: fecha POR TIPO o canal de vazamento de
    conteúdo coletado para `run.stats`/log — em vez de por disciplina (ADR-0009
    item IV, obrigação transversal do item VIII).
    """

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _counters_are_numeric(self) -> Self:
        """Todo contador extra deve ser int/float — string/objeto carregaria conteúdo."""
        for key, value in (self.__pydantic_extra__ or {}).items():
            if not isinstance(value, (int, float)):
                raise ValueError(f"contador {key!r} deve ser numérico, veio {type(value).__name__}")
        return self


class ErrorInfo(BaseModel):
    """Erro estruturado que fecha `run.error` (ADR-0009 item IV).

    `message` é legível e NUNCA deve embutir conteúdo coletado (item VIII);
    `detail` carrega o diagnóstico estruturado que não cabe na mensagem.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    message: str
    detail: dict[str, Any] | None = None


class RunResult(BaseModel):
    """Envelope que o worker devolve; o runtime persiste (ADR-0009 item III).

    `payloads` e `error` podem coexistir: o runtime persiste os payloads
    entregues e SÓ DEPOIS fecha o run como erro — falha parcial deixa itens
    já gravados no lugar (ADR-0009 item VII).
    """

    model_config = ConfigDict(extra="forbid")

    payloads: list[Payload] = []
    stats: Stats = Field(default_factory=Stats)
    error: ErrorInfo | None = None
