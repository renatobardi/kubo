"""Flow runner FINO — camada de bookkeeping de grafo ACIMA de `run_worker` (ADR-0016 §III).

O runner NÃO executa nada: instancia o flow (congela o snapshot + materializa personas),
cria o task, transiciona, delega ao ÚNICO mecanismo de execução (`run_worker`) e transiciona
de novo segundo o status do run. O binding template→comportamento é o `FLOW_REGISTRY`
hardcoded (E4, precedente do WORKER_REGISTRY do ADR-0010): template novo = código + PR.

Síncrono no processo do chamador (CLI): `run_flow` bloqueia até entregar. Fila/polling/claim
= orquestrador (escopo negativo §1.2) — NÃO. Crash deixa task em `analyzing` (órfão visível,
sem janitor); re-execução = novo flow.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from surrealdb import RecordID

from kubo.contracts.models import WorkerManifest
from kubo.distribution.destinations import ResolvedDestination
from kubo.embedding import Embedder
from kubo.errors import ConfigError
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.executors.base import Executor
from kubo.runtime.flow_templates import FlowTemplate, load_flow_templates
from kubo.runtime.personas import Persona, load_personas
from kubo.runtime.runner import FlowCtx, run_worker
from kubo.store.flows import create_task, instantiate_flow, set_task_run, transition_task
from kubo.store.knowledge import run_status
from kubo.workers.analyst import AnalystWorker, Sender

_CATALOG_ROOT = Path(__file__).parents[2] / "catalogs"
_TEMPLATES_DIR = _CATALOG_ROOT / "flow_templates"
_PERSONAS_DIR = _CATALOG_ROOT / "personas"

# summary + análise no relatório precisa de folga (R4); o default 1024 truncaria.
_REPORT_MAX_TOKENS = 4096
_ANALYST_PERSONA = "analista"
# Estados do board de `analysis` (o handler É análise-específico, E4). As transições
# são validadas contra o snapshot congelado — estes nomes só precisam concordar com o
# template (ambos evoluem no mesmo PR).
_CREATED, _ANALYZING, _DELIVERED, _FAILED = "created", "analyzing", "delivered", "failed"


@dataclass(frozen=True)
class FlowRunResult:
    """O que `run_flow` devolve ao chamador (CLI): o flow instanciado, o task e o run que o
    executou, e o estado final (`delivered`|`failed`)."""

    flow: RecordID
    task: RecordID
    run: RecordID
    state: str


def run_flow(
    db: Any,
    *,
    template_name: str,
    question: str,
    embedder: Embedder,
    destination: ResolvedDestination,
    base_url: str,
    executor: Executor | None = None,
    senders: Mapping[str, Sender] | None = None,
) -> FlowRunResult:
    """Instancia e executa um flow do template `template_name` (ADR-0016 §III).

    Carrega os catálogos, resolve o handler no `FLOW_REGISTRY` (template sem handler falha
    alto — E4) e delega. `executor`/`senders` são injetáveis (default = reais) para tornar o
    caminho testável com LLM/Telegram falsos, como o DigestWorker."""
    templates = load_flow_templates(_TEMPLATES_DIR)
    template = templates.get(template_name)
    if template is None:
        raise ConfigError(f"template de flow '{template_name}' não existe no catálogo")
    handler = _FLOW_REGISTRY.get(template_name)
    if handler is None:
        raise ConfigError(f"template '{template_name}' sem handler no FLOW_REGISTRY")
    personas = load_personas(_PERSONAS_DIR)
    return handler(
        db,
        template,
        personas,
        question,
        embedder=embedder,
        destination=destination,
        base_url=base_url,
        executor=executor,
        senders=senders,
    )


def _run_analysis(
    db: Any,
    template: FlowTemplate,
    personas: Mapping[str, Persona],
    question: str,
    *,
    embedder: Embedder,
    destination: ResolvedDestination,
    base_url: str,
    executor: Executor | None,
    senders: Mapping[str, Sender] | None,
) -> FlowRunResult:
    """Comportamento do template `analysis` (E4): a analista recebe o task; o humano é
    materializado (D33) mas não recebe. Instancia → analyzing → run_worker(AnalystWorker) →
    delivered|failed segundo o run."""
    analista = personas.get(_ANALYST_PERSONA)
    if analista is None:
        raise ConfigError(f"template 'analysis' exige a persona '{_ANALYST_PERSONA}' no catálogo")
    _assert_permissions(analista, AnalystWorker.manifest)
    resolved_executor = executor if executor is not None else _build_executor(analista)

    inst = instantiate_flow(db, template=template, personas=personas, question=question)
    task = create_task(db, flow=inst.flow, persona=inst.personas[_ANALYST_PERSONA], state=_CREATED)
    transition_task(db, task, from_state=_CREATED, to_state=_ANALYZING)

    worker = AnalystWorker(
        resolved_executor,
        prompt=analista.prompt,
        destination=destination,
        base_url=base_url,
        senders=senders,
    )
    run_id = run_worker(
        db,
        worker,
        config={"question": question},
        embedder=embedder,
        flow_ctx=FlowCtx(inst.flow, task),
    )
    set_task_run(db, task, run_id)
    final = _DELIVERED if _run_succeeded(db, run_id) else _FAILED
    transition_task(db, task, from_state=_ANALYZING, to_state=final)
    return FlowRunResult(flow=inst.flow, task=task, run=run_id, state=final)


def _assert_permissions(persona: Persona, manifest: WorkerManifest) -> None:
    """R6 (least-privilege, ADR-0016 §IX): a persona deve declarar toda integração que o
    manifest do worker exige, senão ConfigError. Enforcement unificado por persona é fase 3."""
    missing = sorted(set(manifest.integrations) - set(persona.permissions))
    if missing:
        raise ConfigError(f"persona '{persona.name}' sem permissão para integrações: {missing}")


def _build_executor(persona: Persona) -> Executor:
    """Constrói o executor de LLM da persona (modelo congelado do catálogo = gate humano por
    PR). Só `api` nesta fase; `cli` é 0015."""
    if persona.executor != "api":
        raise ConfigError(
            f"executor '{persona.executor}' da persona '{persona.name}' não suportado (só api)"
        )
    if not persona.model:
        raise ConfigError(f"persona '{persona.name}' (executor api) sem model")
    return ApiExecutor(ApiExecutorConfig(model=persona.model, max_tokens=_REPORT_MAX_TOKENS))


def _run_succeeded(db: Any, run_id: RecordID) -> bool:
    """True se o run fechou `ok` — decide delivered vs failed (o run é a fonte da verdade
    do resultado da execução; o flow runner só reflete no estado do task). A leitura passa
    pela store (`run_status`, invariante 2), não por `db.query` direto aqui."""
    return run_status(db, run_id) == "ok"


# Binding template→comportamento HARDCODED (E4). Template novo = função nova + PR (gate humano).
_FLOW_REGISTRY: dict[str, Callable[..., FlowRunResult]] = {"analysis": _run_analysis}
