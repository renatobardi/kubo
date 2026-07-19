"""Despacho do sweep de coleta dirigido por Cadastro (#108, ADR-0025 §4/§7).

O sweep é query→loop→run_worker: o BANCO diz O QUÊ coletar (Cadastros ativos), o CÓDIGO diz
COMO (o mapa `SWEEP_DISPATCH` abaixo, kind→worker+config), o RELÓGIO FIXO diz QUANDO (o cron
da `SweepEntry` no schedules.yaml). Enquanto for isso, fica do lado de dentro do invariante 7
(sem workflow engine / orquestrador). As linhas que NÃO se cruzam — cada uma reabriria o
escopo negativo, anote para o você-de-6-meses:
  (a) cron por-Cadastro no banco → o banco passaria a dizer "quando";
  (b) worker/config por-Cadastro como campo livre do Cadastro → o banco diria "como";
  (c) retry/backoff/estado de orquestração persistido entre runs;
  (d) dependência entre runs ("roda X depois de Y").
O mapa é fixo em código (ADR-0025 §7): NUNCA nome-de-worker como dado do Cadastro. O #110
adiciona a chave `github-repo` (coletor de releases); hoje o sweep só despacha `rss`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kubo.store.knowledge import ActiveSource
from kubo.workers.feed import FeedWorker


@dataclass(frozen=True)
class KindDispatch:
    """Como despachar um kind de Cadastro no sweep: a factory do worker (uma instância nova por
    run) + o construtor da config do worker a partir do Cadastro. Um por kind despachável (o
    kind em si é a CHAVE do `SWEEP_DISPATCH`, não um campo daqui)."""

    worker_factory: Callable[[], Any]
    build_config: Callable[[ActiveSource], dict[str, Any]]


def _feed_config(source: ActiveSource) -> dict[str, Any]:
    """Config do worker `feed` a partir de um Cadastro rss: a canonical É o feed_url; title e
    tags reproduzem o que o `schedules.yaml` passava (tags → metadata dos itens no feed)."""
    return {"feed_url": source.canonical, "title": source.title, "tags": source.tags}


# Mapa fixo kind→despacho. `rss`→worker `feed`. Chave nova = código + PR (ADR-0025 §7,
# teste do PR do invariante 7): tipo novo de coleta é código, não dado.
SWEEP_DISPATCH: dict[str, KindDispatch] = {
    "rss": KindDispatch(worker_factory=FeedWorker, build_config=_feed_config),
}
