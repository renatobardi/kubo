"""Job da véspera (ADR-0043, KUBO-137): gera a lição do PRÓXIMO dia de estudo.

Roda no fim do dia sobre os planos ATIVOS do par (tenant, user) que o scheduler
resolve — mesmo regime de identidade única do distiller/digest. Idempotente por
construção: o índice UNIQUE (plano, dia) e a checagem prévia deixam as janelas de
retry da mesma véspera seguras.

Falha de LLM não é erro do job: a lição daquele plano fica sem gerar, o log registra
e a próxima janela tenta de novo — travar o job condenaria os planos seguintes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import structlog
from surrealdb import RecordID

_log = structlog.get_logger(__name__)


def generate_upcoming_lessons(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    now: datetime,
    tutor_factory: Callable[[], Any],
) -> list[RecordID]:
    """Gera a lição do próximo dia de estudo de cada plano ativo; devolve os ids criados.

    `now` entra por PARÂMETRO (nunca `datetime.now()` aqui dentro): o dia-alvo é
    derivado dele, e um relógio implícito tornaria o comportamento do job impossível de
    afirmar em teste.

    `tutor_factory` é a costura injetável (molde de `build_planner` nas rotas): em
    produção resolve persona + executor; em teste devolve um fake, então NENHUM teste
    toca LiteLLM.

    Pula, sem erro: plano cuja lição do dia-alvo já existe (idempotência), plano que
    chegou ao fim das lições (completar plano é fatia 5) e plano cujo tutor falhou.
    """
    raise NotImplementedError
