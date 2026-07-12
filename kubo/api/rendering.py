"""Configuração única do Jinja para a UI — autoescape ON (XSS é A ameaça, ADR-0014).

`Jinja2Templates` já autoescapa `.html` por default; o teste via rota real (marco
9.4) prova o comportamento, não a config. `nav` e `current_path` entram em todo
template por context processor — nenhuma rota precisa lembrar de passá-los.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from kubo.api.nav import NAV

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _nav_context(request: Request) -> dict[str, Any]:
    """Injeta `nav` (menu) e `current_path` (para marcar o item ativo) em todo template."""
    return {"nav": NAV, "current_path": request.url.path}


templates = Jinja2Templates(directory=str(_TEMPLATES_DIR), context_processors=[_nav_context])
