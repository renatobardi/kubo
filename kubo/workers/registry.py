"""Registro nome→classe dos workers built-in (ADR-0010; relocado na 0018, ADR-0021 §4).

Mapa HARDCODED: sem registry/plugin/entry-point dinâmico (seria DSL disfarçada, invariante 3).
Ativar/promover um worker = editar este dict + PR (gate humano). Mora aqui — e não em
`kubo/scheduler/` — para que a API (rito de promoção, ADR-0021 §2 import-oráculo) importe o
registro SEM puxar o APScheduler: `worker_name in WORKER_REGISTRY` é o oráculo de "o merge está
na imagem viva". PR de worker vira `kubo/workers/x.py` + 1 linha aqui + testes (E9)."""

from __future__ import annotations

from typing import Any

from kubo.workers.digest import DigestWorker
from kubo.workers.distiller import DistillerWorker
from kubo.workers.feed import FeedWorker
from kubo.workers.github_releases import GithubReleasesWorker

WORKER_REGISTRY: dict[str, type[Any]] = {
    "feed": FeedWorker,
    "distiller": DistillerWorker,
    "digest": DigestWorker,
    "github-releases": GithubReleasesWorker,
}
