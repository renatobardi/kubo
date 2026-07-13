"""Contexto concreto entregue ao worker (ADR-0009 item VI).

Read-only por construção (dataclass frozen): config validada, integrações já
resolvidas (segredo pelo runtime), o adaptador de leitura do grafo
(`GraphKnowledge`, ADR-0013 §III) e o logger bound. O worker NUNCA recebe
handle de `db` — persistir é do runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from surrealdb import RecordID

from kubo.contracts.worker import DigestView, ItemView, RetrievedView
from kubo.embedding import Embedder
from kubo.runtime.integrations import ResolvedIntegration
from kubo.store.knowledge import distilled_for_digest, items_without_distilled, search_distilled


class GraphKnowledge:
    """Adaptador read-only que o runner injeta no ctx (ADR-0013 §III.2).

    Guarda o mapa ref (opaco, int) -> RecordID de todo item entregue via
    `items_to_distill`. `resolve` fica FORA do Protocol `KnowledgeReader` — o
    worker nunca o vê; só o runner (na hora de persistir `DistilledPayload`,
    Peça 6) o chama. Nasce POR RUN: cada `run_worker` cria uma instância nova,
    o que mata estado compartilhado entre execuções.
    """

    def __init__(self, db: Any) -> None:
        """Guarda o handle de `db` (só a store o usa) e zera o mapa de refs."""
        self._db = db
        self._ref_map: dict[int, RecordID] = {}
        self._counter = 0

    def items_to_distill(self, limit: int) -> list[ItemView]:
        """Lê itens pendentes via store e atribui a cada um um `ref` opaco,
        sequencial e MONOTÔNICO por-instância (nunca reseta entre chamadas)."""
        rows = items_without_distilled(self._db, limit=limit)
        views: list[ItemView] = []
        for rid, title, content in rows:
            ref = self._counter
            self._counter += 1
            self._ref_map[ref] = rid
            views.append(ItemView(ref=ref, title=title, content=content))
        return views

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[RetrievedView]:
        """Busca semântica no acervo para a analista (ADR-0016 §III): delega à store
        (`search_distilled`, que reusa o KNN único) e mapeia cada `RetrievedDoc` a
        `RetrievedView` — o id vira forma STRING opaca (`distilled:<hex>`), a única
        exposição de id ao worker (entra em `consulted`/citação, vem do retrieval)."""
        return [
            RetrievedView(id=str(doc.id), title=doc.title, summary=doc.summary)
            for doc in search_distilled(self._db, embedding=embedding, k=k)
        ]

    def resolve(self, ref: int) -> RecordID | None:
        """Resolve um `ref` opaco ao `RecordID` real, ou `None` se não existe
        (§III.6: ref não-resolvível é ErrorInfo por-payload no runner — `resolve`
        nunca levanta)."""
        return self._ref_map.get(ref)

    def distilled_for_digest(self, destination: str, limit: int) -> list[DigestView]:
        """Seleção de digest (ADR-0015 §IV): delega à store (que resolve watermark +
        bootstrap) e mapeia cada `DigestRow` a `DigestView` — o id vira forma STRING
        opaca (`distilled:<hex>`), a única exposição de id ao digest worker."""
        return [
            DigestView(
                id=str(row.id),
                title=row.title,
                summary=row.summary,
                created_at=row.created_at,
                entities=row.entities,
            )
            for row in distilled_for_digest(self._db, destination=destination, limit=limit)
        ]


@dataclass(frozen=True)
class RunContext:
    """Contexto read-only do worker. Satisfaz o Protocol `RunContext` do contrato.

    `config` é a instância validada do schema declarado no manifest (o worker
    faz o narrowing para o tipo concreto). `integrations` traz só as declaradas
    ∩ existentes, com segredo já resolvido. `logger` é bound com run_id/worker e
    NUNCA carrega payload coletado (ADR-0009 item VIII). `embedder` é o seam de
    geração de embeddings (ADR-0013 §III.5) — `None` para workers que não
    embeddam; fica por último com default para não forçar default nos campos
    anteriores.
    """

    config: BaseModel
    integrations: Mapping[str, ResolvedIntegration]
    knowledge: GraphKnowledge
    logger: Any
    embedder: Embedder | None = field(default=None)
