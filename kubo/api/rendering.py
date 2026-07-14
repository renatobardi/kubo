"""Configuração única do Jinja para a UI — autoescape ON (XSS é A ameaça, ADR-0014).

`Jinja2Templates` já autoescapa `.html` por default; o teste via rota real (marco
9.4) prova o comportamento, não a config. `nav` e `current_path` entram em todo
template por context processor — nenhuma rota precisa lembrar de passá-los.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from kubo.api.nav import NAV, NavItem

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _current_nav_item(path: str) -> NavItem | None:
    """Item de nav correspondente ao path atual — casa exato ou como prefixo (detalhe:
    `/entities/xyz` casa `/entities`). Alimenta o breadcrumb da barra de topo."""
    if path == "/":
        return next((i for i in NAV if i["route"] == "/"), None)
    candidates = [
        i
        for i in NAV
        if i["route"] != "/" and (path == i["route"] or path.startswith(i["route"] + "/"))
    ]
    return max(candidates, key=lambda i: len(i["route"]), default=None)


def _nav_context(request: Request) -> dict[str, Any]:
    """Injeta `nav` (menu), `current_path` (item ativo) e `crumb` (breadcrumb da barra
    de topo: grupo › rótulo da tela atual) em todo template."""
    return {
        "nav": NAV,
        "current_path": request.url.path,
        "crumb": _current_nav_item(request.url.path),
    }


def _parse(iso: str | None) -> datetime | None:
    """Parseia um carimbo ISO (a store devolve `str(datetime)`); None se ausente/ilegível.
    Carimbo naive é assumido UTC (o storage é sempre UTC) para que a conversão de tela
    seja consistente — nunca reinterpretado como hora local."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local(dt: datetime) -> datetime:
    """Converte um carimbo (UTC) para a tz de apresentação: env `TZ`, default
    America/Sao_Paulo. Regra: todo datetime formatado para humano passa por aqui;
    o que é armazenado/comparado permanece UTC."""
    return dt.astimezone(ZoneInfo(os.environ.get("TZ") or "America/Sao_Paulo"))


def short_datetime(iso: str | None) -> str:
    """Carimbo curto para exibição ('Jul 12, 09:00'); '—' se ausente/ilegível."""
    dt = _parse(iso)
    return _local(dt).strftime("%b %d, %H:%M") if dt else "—"


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
