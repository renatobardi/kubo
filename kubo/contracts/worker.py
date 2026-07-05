"""Protocol do contrato de worker e validação de runtime (ADR-0009).

`Worker`/`RunContext`/`KnowledgeReader` são Protocols — servem à checagem
estática do pyright, NÃO validam nada em runtime (`isinstance`/
`@runtime_checkable` só checam presença de membros, não a forma do manifest
nem a assinatura de `run` — falsa validação de uma fronteira de segurança,
ADR-0009 item I). A validação de runtime é a função explícita
`validate_worker`.

"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from kubo.contracts.models import RunResult, WorkerManifest
from kubo.errors import ContractError

_MISSING = object()


class KnowledgeReader(Protocol):
    """Seam de leitura do grafo, vazio na fase 1 (ADR-0009 item VI).

    Métodos entram quando um worker exigir leitura do grafo, com teste que
    justifique — não se especula agora.
    """


class RunContext(Protocol):
    """Contexto somente-leitura entregue ao worker (ADR-0009 item VI).

    O worker nunca recebe handle de `db` — persistir é do runtime.
    """

    config: BaseModel
    # Mapping (não dict): atributo de Protocol é invariante — o ctx concreto usa
    # Mapping[str, ResolvedIntegration] e precisa satisfazer este Protocol.
    integrations: Mapping[str, Any]
    knowledge: KnowledgeReader
    logger: Any


class Worker(Protocol):
    """O que o pyright vê: manifest declarado + `run(ctx) -> RunResult` (ADR-0009 item I)."""

    manifest: WorkerManifest

    def run(self, ctx: RunContext) -> RunResult: ...


def _safe_getattr(obj: object, name: str) -> object:
    """Lê um atributo tratando QUALQUER falha como ausência.

    `getattr(obj, name, default)` só cai no default em AttributeError — uma
    property/descriptor hostil que levanta outra exceção propagaria e faria
    `validate_worker` explodir (em vez de rejeitar com ContractError). Aqui, um
    worker não-confiável que estoura no acesso vira simplesmente "ausente"."""
    try:
        return getattr(obj, name, _MISSING)
    except Exception:  # noqa: BLE001 — fronteira: descriptor hostil vira ausência, não crash
        return _MISSING


def validate_worker(obj: object) -> WorkerManifest:
    """Valida que `obj` honra o contrato de worker; retorna o manifest validado.

    Checa: (a) `obj.manifest` existe e é validável como `WorkerManifest`; (b)
    `obj.run` é callable com a assinatura esperada (um parâmetro posicional
    além de `self`). Falha em qualquer uma das duas condições levanta
    `ContractError` (ADR-0009 item V).

    O retorno é o manifest VALIDADO — o runner usa esse retorno e nunca relê
    `obj.manifest` depois, fechando o TOCTOU de um worker hostil que expõe
    `manifest` como property inconsistente entre leituras.
    """
    raw = _safe_getattr(obj, "manifest")
    if raw is _MISSING:
        raise ContractError("worker não expõe um atributo `manifest` legível")
    try:
        manifest = WorkerManifest.model_validate(raw)
    except ValidationError as exc:
        # Sem str(exc): não propaga o input_value (que poderia carregar valor
        # sensível colado no manifest) para o ContractError/log.
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        raise ContractError(f"manifest do worker é inválido (campos: {fields})") from exc

    run = _safe_getattr(obj, "run")
    if not callable(run):
        raise ContractError("worker.run não é callable")
    try:
        signature = inspect.signature(run)
    except (TypeError, ValueError) as exc:  # callable sem assinatura inspecionável
        raise ContractError("worker.run não tem assinatura inspecionável") from exc
    # `run` é lido como método vinculado — `self` já não aparece; sobra só o ctx.
    positional = [
        p
        for p in signature.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) != 1:
        raise ContractError(
            f"worker.run deve aceitar exatamente um parâmetro (ctx); tem {len(positional)}"
        )
    return manifest
