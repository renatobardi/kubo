"""Parâmetros de paginação da UI (0011): tamanho de página 50/100 + start, vindos de
query param (hostis na borda) e clampados aqui — a rota nunca confia no valor cru.

`_SIZES` são os tamanhos permitidos (D-d: 50/100, sem passar de `_MAX_PAGE=100` da
store). Tamanho fora da lista cai no default; start negativo vira 0."""

from __future__ import annotations

_SIZES = (50, 100)
_DEFAULT_SIZE = 50


def clamp_size(size: int) -> int:
    """Tamanho de página válido (50/100); qualquer outro vira o default."""
    return size if size in _SIZES else _DEFAULT_SIZE


def clamp_start(start: int) -> int:
    """Offset da página; negativo vira 0."""
    return max(0, int(start))
