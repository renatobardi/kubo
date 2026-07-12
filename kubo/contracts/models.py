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

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    name: str
    version: str
    schema_version: Literal[1] = 1
    integrations: list[str] = Field(default_factory=list)
    config: type[BaseModel]


class SourcePayload(BaseModel):
    """Espelha `upsert_source(kind=..., canonical=..., title=...)`."""

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

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

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    type: Literal["item"] = "item"
    source: SourcePayload
    external_id: str
    content: str
    url: str | None = None
    title: str | None = None
    metadata: dict[str, Any] | None = None


class EntityRef(BaseModel):
    """Entidade citada num distilled, referenciada por NOME (ADR-0013 §III.4).

    O runner resolve nome→RecordID via `get_or_create_entity` (UPSERT idempotente
    por chave natural) — o worker nunca vê RecordID, só declara o nome. Cercas
    de volume porque `mentions` é permanente: um nome degenerado (10 KB, ou em
    volume) viraria aresta permanente no grafo, não uma linha descartável.
    """

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    name: str = Field(min_length=1, max_length=200)
    kind: str | None = Field(default=None, max_length=50)


class ChunkPayload(BaseModel):
    """Espelha 1:1 o dataclass `Chunk` de `kubo/store/knowledge.py` (ADR-0013 §III.5).

    Chunk já embeddado que o worker monta e devolve dentro do `DistilledPayload`
    — o runner grava via `insert_distilled`, que já cobre chunks na mesma
    transação.
    """

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    text: str
    seq: int
    embedding: list[float]
    model: str
    dim: int
    task_type: str

    @model_validator(mode="after")
    def _dim_matches_embedding_length(self) -> Self:
        """`dim` deve bater com `len(embedding)` — fronteira de segurança mais perto
        do worker que montou o chunk; a store já valida de novo, mas falhar aqui é
        mais rápido e não depende do caminho de escrita. Mensagem clara, sem embutir
        o embedding (poderia carregar volume grande de floats)."""
        if self.dim != len(self.embedding):
            raise ValueError(
                f"dim ({self.dim}) não bate com o tamanho do embedding ({len(self.embedding)})"
            )
        return self


class DistilledPayload(BaseModel):
    """Resultado da destilação de um item (ADR-0013 §III.2).

    `ref` é o ref OPACO que `items_to_distill` devolveu — o runner resolve
    ref→RecordID e chama `insert_distilled`; o worker nunca vê RecordID (item 2/3).
    Cercas de volume em `summary`/`entities` por tipo (advisor); `claims` fica
    de fora por design — sem consumidor ainda (D23). `chunks` não tem teto:
    o volume de chunks é função do texto de origem, já limitado na origem.
    """

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    type: Literal["distilled"] = "distilled"
    schema_version: Literal[1] = 1
    ref: int
    summary: str = Field(min_length=1, max_length=8000)
    entities: list[EntityRef] = Field(default_factory=lambda: [], max_length=20)
    chunks: list[ChunkPayload] = Field(default_factory=lambda: [])


Payload: TypeAlias = Annotated[
    SourcePayload | ItemPayload | DistilledPayload, Field(discriminator="type")
]


class Stats(BaseModel):
    """Contadores livres do worker — envelope tipado sobre `run.stats`.

    Permissivo nos NOMES (cada worker conta métricas próprias), mas rejeita
    valor extra que não seja numérico: fecha POR TIPO o canal de vazamento de
    conteúdo coletado para `run.stats`/log — em vez de por disciplina (ADR-0009
    item IV, obrigação transversal do item VIII).
    """

    model_config = ConfigDict(extra="allow", revalidate_instances="always")

    @model_validator(mode="after")
    def _counters_are_numeric(self) -> Self:
        """Todo contador extra deve ser int/float — string/objeto carregaria conteúdo."""
        for key, value in (self.__pydantic_extra__ or {}).items():
            # bool é subclasse de int — exclui explícito: um contador não é flag, e o
            # rigor máximo da fronteira não deixa `bool` passar como "numérico".
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"contador {key!r} deve ser numérico, veio {type(value).__name__}")
        return self


class ErrorInfo(BaseModel):
    """Erro estruturado que fecha `run.error` (ADR-0009 item IV).

    `message` é legível e NUNCA deve embutir conteúdo coletado (item VIII);
    `detail` carrega o diagnóstico estruturado que não cabe na mensagem.
    """

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    kind: str
    # Teto de 500: fecha POR TIPO o vazamento de conteúdo coletado, mesmo quando o
    # worker RETORNA o erro (não só quando o runner o constrói do exception).
    message: str = Field(max_length=500)
    detail: dict[str, Any] | None = None


class RunResult(BaseModel):
    """Envelope que o worker devolve; o runtime persiste (ADR-0009 item III).

    `payloads` e `error` podem coexistir: o runtime persiste os payloads
    entregues e SÓ DEPOIS fecha o run como erro — falha parcial deixa itens
    já gravados no lugar (ADR-0009 item VII).
    """

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    payloads: list[Payload] = []
    stats: Stats = Field(default_factory=Stats)
    error: ErrorInfo | None = None
