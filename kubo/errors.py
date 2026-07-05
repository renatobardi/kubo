"""Exceções específicas do domínio Kubo.

Erros do runtime/worker retornam estruturados em RunResult (CLAUDE.md); estas
exceções são para falhas de configuração/programação que devem interromper.
"""

from __future__ import annotations


class KuboError(Exception):
    """Base de todas as exceções do domínio Kubo."""


class ConfigError(KuboError):
    """Configuração ausente ou inconsistente (ex.: credencial obrigatória faltando)."""


class StoreError(KuboError):
    """Falha na camada de acesso ao datastore (ex.: statement revertido numa transação).

    O SurrealDB 3.x reverte uma transação com erro no meio mas NÃO propaga a falha
    via `query()` (ADR-0005); o wrapper transacional da store inspeciona todos os
    statements e levanta este erro quando algum falhou.
    """
