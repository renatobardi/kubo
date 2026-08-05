"""Pinos de LLM compartilhados entre routes e scheduler (ADR-0049 §III).

O gate humano vive no cron, não no catálogo — o modelo e o teto de tokens do
summarizer são os mesmos no upload síncrono (legado) e no job de ingestão.
"""

from __future__ import annotations

DEFAULT_MODEL = "anthropic/claude-haiku-4-5"
SUMMARY_MAX_TOKENS = 1024
