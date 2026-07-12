"""Configuração única do Jinja para a UI — autoescape ON (XSS é A ameaça, ADR-0014).

`Jinja2Templates` já autoescapa `.html` por default; o teste via rota real (marco
9.4) prova o comportamento, não a config. `nav` e `current_path` entram em todo
template por context processor — nenhuma rota precisa lembrar de passá-los.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from kubo.api.nav import NAV

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _nav_context(request: Request) -> dict[str, Any]:
    """Injeta `nav` (menu) e `current_path` (para marcar o item ativo) em todo template."""
    return {"nav": NAV, "current_path": request.url.path}


def _parse(iso: str | None) -> datetime | None:
    """Parseia um carimbo ISO (a store devolve `str(datetime)`); None se ausente/ilegível."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def short_datetime(iso: str | None) -> str:
    """Carimbo curto para exibição ('Jul 12, 09:00'); '—' se ausente/ilegível."""
    dt = _parse(iso)
    return dt.strftime("%b %d, %H:%M") if dt else "—"


def duration(start: str | None, end: str | None) -> str:
    """Duração humana entre dois carimbos ISO ('48s', '2m 11s', '1h 03m'); '—' quando
    o run ainda não terminou (end None) ou os carimbos não parseiam."""
    a, b = _parse(start), _parse(end)
    if a is None or b is None:
        return "—"
    secs = int((b - a).total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60:02d}s"
    return f"{secs // 3600}h {(secs % 3600) // 60:02d}m"


def days_since(iso: str | None) -> int | None:
    """Dias inteiros desde um carimbo ISO até agora; None se ausente — insumo do badge
    de recência das Fontes (E4: mostra o FATO 'última coleta há Nd', não julga saúde)."""
    dt = _parse(iso)
    if dt is None:
        return None
    now = datetime.now(dt.tzinfo)
    return max(0, (now - dt).days)


templates = Jinja2Templates(directory=str(_TEMPLATES_DIR), context_processors=[_nav_context])
templates.env.filters["short_datetime"] = short_datetime
templates.env.filters["duration"] = duration
templates.env.filters["days_since"] = days_since
