"""Rota de Destinos (ADR-0015, paridade `DestinosScreen` do DistribuicaoScreen.jsx):
o que é distribuído (artefatos configurados) e para onde (destinos).

Tela de CONFIGURAÇÃO, lida dos YAML declarativos (destinations.yaml + schedules.yaml)
— não do banco. Não resolve `address_ref` (PII fica fora da UI; mostra só id/nome/
canal/kind). Desvios pré-declarados do plano: "Novo artefato" fora de escopo (config
é YAML, editar = editar arquivo + PR); badge de convidado não existe (não há
convidados nesta fase — D11 os prevê, não implementados).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from starlette.responses import Response

from kubo.api.rendering import templates
from kubo.distribution.destinations import Destination, load_destinations

router = APIRouter()

_REPO_ROOT = Path(__file__).parents[3]
_DESTINATIONS_PATH = _REPO_ROOT / "destinations.yaml"
_SCHEDULES_PATH = _REPO_ROOT / "schedules.yaml"


@dataclass(frozen=True)
class Artefato:
    """Um artefato recorrente configurado (o digest): nome, agenda humana, origem e
    os destinos que o recebem — derivado do schedules.yaml + destinations.yaml."""

    name: str
    agenda: str
    origem: str
    destinos: list[str]


@router.get("")
def list_page(request: Request) -> Response:
    """Página de Destinos: artefatos configurados (do schedules.yaml) + destinos
    declarados (do destinations.yaml). Sem banco — leitura de config declarativa."""
    destinations = load_destinations(_DESTINATIONS_PATH)
    artefatos = _digest_artefatos(_SCHEDULES_PATH, destinations)
    return templates.TemplateResponse(
        request,
        "destinations/list.html",
        {"destinations": destinations, "artefatos": artefatos},
    )


def _digest_artefatos(schedules_path: Path, destinations: list[Destination]) -> list[Artefato]:
    """Deriva os artefatos configurados dos entries `digest` do schedules.yaml.

    O digest envia para TODOS os destinos declarados (um artefato, um conteúdo nesta
    fase — sem digest por-destino). `agenda` traduz o cron para leitura humana."""
    raw = yaml.safe_load(schedules_path.read_text(encoding="utf-8"))
    entries = raw.get("schedules", []) if isinstance(raw, dict) else []
    nomes = [d.name for d in destinations]
    artefatos: list[Artefato] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("worker") == "digest":
            artefatos.append(
                Artefato(
                    name="Digest",
                    agenda=_humanize_cron(str(entry.get("cron", ""))),
                    origem="destilados novos desde o último envio",
                    destinos=nomes,
                )
            )
    return artefatos


def _humanize_cron(cron: str) -> str:
    """Traduz um cron diário `M H * * *` para 'diário às HH:MM'; senão devolve o cron cru."""
    parts = cron.split()
    if (
        len(parts) == 5
        and parts[2:] == ["*", "*", "*"]
        and parts[0].isdigit()
        and parts[1].isdigit()
    ):
        return f"diário às {int(parts[1]):02d}:{int(parts[0]):02d}"
    return cron or "—"
