"""Camada autorizada de configuração de LLM (ADR-0054 §IX).

Registry, resolvedor e utilitários que decidem o que chega a uma chamada de
modelo. Nenhum outro módulo deve escolher modelo ou parâmetro por conta própria.
"""

from __future__ import annotations

from kubo.llm.registry import get_capabilities

__all__ = ["get_capabilities"]
