"""
Contratos e validação — protocolos de workers, manifestos, schemas.

Define as interfaces obrigatórias que workers devem implementar,
regras de validação de manifestos de integração e enforcement de tipos.
"""

from __future__ import annotations

from kubo.contracts.models import (
    ErrorInfo,
    ItemPayload,
    Payload,
    RunResult,
    SourcePayload,
    Stats,
    WorkerManifest,
)
from kubo.contracts.worker import KnowledgeReader, RunContext, Worker, validate_worker

__all__ = [
    "ErrorInfo",
    "ItemPayload",
    "KnowledgeReader",
    "Payload",
    "RunContext",
    "RunResult",
    "SourcePayload",
    "Stats",
    "Worker",
    "WorkerManifest",
    "validate_worker",
]
