"""Contexto concreto entregue ao worker (ADR-0009 item VI).

Read-only por construção (dataclass frozen): config validada, integrações já
resolvidas (segredo pelo runtime), o seam de leitura do grafo (vazio na fase 1)
e o logger bound. O worker NUNCA recebe handle de `db` — persistir é do runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from kubo.runtime.integrations import ResolvedIntegration


class EmptyKnowledge:
    """Seam de leitura do grafo, VAZIO na fase 1 (ADR-0009 item VI).

    Materializa em código a alternativa rejeitada (d) do ADR: leitura do grafo
    passa por AQUI e nunca por handle de `db`. Métodos entram quando um worker
    exigir leitura, com teste que justifique — não se especula agora.
    """


@dataclass(frozen=True)
class RunContext:
    """Contexto read-only do worker. Satisfaz o Protocol `RunContext` do contrato.

    `config` é a instância validada do schema declarado no manifest (o worker
    faz o narrowing para o tipo concreto). `integrations` traz só as declaradas
    ∩ existentes, com segredo já resolvido. `logger` é bound com run_id/worker e
    NUNCA carrega payload coletado (ADR-0009 item VIII).
    """

    config: BaseModel
    integrations: Mapping[str, ResolvedIntegration]
    knowledge: EmptyKnowledge
    logger: Any
