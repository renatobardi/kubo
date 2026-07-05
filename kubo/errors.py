"""Exceções específicas do domínio Kubo.

Erros do runtime/worker retornam estruturados em RunResult (CLAUDE.md); estas
exceções são para falhas de configuração/programação que devem interromper.
"""

from __future__ import annotations


class KuboError(Exception):
    """Base de todas as exceções do domínio Kubo."""


class ConfigError(KuboError):
    """Configuração ausente ou inconsistente (ex.: credencial obrigatória faltando)."""
