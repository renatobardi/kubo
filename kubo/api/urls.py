"""Helpers compartilhados de URL/routing para rotas da UI."""

from __future__ import annotations

from urllib.parse import urlparse


def safe_next(raw: str, default: str = "/") -> str:
    """Só aceita paths relativos locais como destino pós-login."""
    if not raw:
        return default
    path = urlparse(raw).path
    if not path.startswith("/") or path.startswith("//"):
        return default
    return path
