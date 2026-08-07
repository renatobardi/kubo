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

from kubo.contracts.worker import DigestSelectionView, DigestView, ItemView, RetrievedView
from kubo.embedding import Embedder
from kubo.runtime.integrations import ResolvedIntegration
from kubo.store.destinations import record_id_from_destination
from kubo.store.knowledge import (
    DistilledListItem,
    DistilledView,
    items_for_digest,
    list_distilled,
    read_distilled,
    search_distilled,
)
from kubo.store.knowledge import (
    items_to_score as store_items_to_score,
)
from kubo.store.tenancy import get_tenant_work_context


class GraphKnowledge:
    """Adaptador read-only que o runner injeta no ctx (ADR-0013 §III.2).

    Guarda o mapa ref (opaco, int) -> RecordID de todo item entregue via
    `items_to_score`. `resolve` fica FORA do Protocol `KnowledgeReader` — o
    worker nunca o vê; só o runner (na hora de persistir `DistilledPayload`,
    Peça 6) o chama. Nasce POR RUN: cada `run_worker` cria uma instância nova,
    o que mata estado compartilhado entre execuções.
    """

    def __init__(
        self,
        db: Any,
        *,
        tenant_id: RecordID,
        user_id: RecordID,
    ) -> None:
        """Guarda o handle de `db` (só a store o usa), os ids de tenancy e zera o mapa de refs."""
        self._db = db
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._ref_map: dict[int, RecordID] = {}
        self._counter = 0

    def items_to_score(self, limit: int) -> list[ItemView]:
        """Lê itens pendentes de pontuação via store (ADR-0051 §I) e atribui a
        cada um um `ref` opaco, sequencial e MONOTÔNICO por-instância (nunca
        reseta entre chamadas)."""
        rows = store_items_to_score(
            self._db,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            limit=limit,
        )
        views: list[ItemView] = []
        for rid, title, url, content in rows:
            ref = self._counter
            self._counter += 1
            self._ref_map[ref] = rid
            views.append(ItemView(ref=ref, title=title, url=url, content=content))
        return views

    def work_context(self) -> str:
        """Contexto de trabalho do dono do tenant desta instância (ADR-0051 §I.1) —
        delega à store, o worker nunca vê o RecordID do tenant."""
        return get_tenant_work_context(self._db, self._tenant_id)

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[RetrievedView]:
        """Busca semântica no acervo para a analista (ADR-0016 §III): delega à store
        (`search_distilled`, que reusa o KNN único) e mapeia cada `RetrievedDoc` a
        `RetrievedView` — o id vira forma STRING opaca (`distilled:<hex>`), a única
        exposição de id ao worker (entra em `consulted`/citação, vem do retrieval)."""
        return [
            RetrievedView(id=str(doc.id), title=doc.title, summary=doc.summary)
            for doc in search_distilled(
                self._db,
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                embedding=embedding,
                k=k,
            )
        ]

    def resolve(self, ref: int) -> RecordID | None:
        """Resolve um `ref` opaco ao `RecordID` real, ou `None` se não existe
        (§III.6: ref não-resolvível é ErrorInfo por-payload no runner — `resolve`
        nunca levanta)."""
        return self._ref_map.get(ref)

    def read_distilled(self, distilled: RecordID) -> DistilledView | None:
        """Lê a visão completa de proveniência de um distilled (ADR-0013 §8.5),
        escopada ao tenant quando os ids de tenancy estão presentes."""
        return read_distilled(
            self._db,
            distilled,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
        )

    def list_distilled(self, *, limit: int, start: int) -> list[DistilledListItem]:
        """Página do acervo de destilados, filtrada pelo tenant quando os ids
        de tenancy estão presentes."""
        return list_distilled(
            self._db,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            limit=limit,
            start=start,
        )

    def items_for_digest(self, destination: str, limit: int) -> DigestSelectionView:
        """Digest selection by publication window (ADR-0050): delegates to the store
        (which resolves the window, exclusion, dedup, ordering and cut) and maps each
        `DigestItemView` to `DigestView` — the id becomes an opaque STRING
        (`item:<hex>`), the only id exposure to the digest worker.

        `destination` arrives as a `destination:<key>` string from the worker; convert
        it to a `RecordID` before calling the store (KUBO-48 cutover)."""
        selection = items_for_digest(
            self._db,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            destination=record_id_from_destination(destination),
            limit=limit,
        )
        return DigestSelectionView(
            form=selection.form,
            items=[
                DigestView(
                    id=str(item.id),
                    title=item.title,
                    summary=item.summary,
                    score=item.score,
                    published_at=item.published_at,
                    url=item.url,
                    entities=item.entities,
                )
                for item in selection.items
            ],
            window_start=selection.window_start,
            window_end=selection.window_end,
            watermark=selection.watermark,
            total_publications=selection.total_publications,
        )


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
