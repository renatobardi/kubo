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

import structlog
from surrealdb import RecordID

from kubo.contracts.models import WorkerManifest
from kubo.distribution.destinations import ResolvedDestination
from kubo.distribution.telegram import send_telegram
from kubo.embedding import Embedder
from kubo.errors import ConfigError, SenderError, StateError
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.executors.base import Executor
from kubo.runtime.flow_templates import FlowTemplate, load_flow_templates
from kubo.runtime.integrations import load_integrations, resolve_integrations
from kubo.runtime.personas import Persona, load_personas
from kubo.runtime.runner import FlowCtx, run_worker
from kubo.store.flows import (
    create_task,
    decide_gate,
    instantiate_flow,
    open_gate,
    read_gate_context,
    set_task_run,
    template_of_task,
    transition_task,
)
from kubo.store.knowledge import insert_dispatch, run_status
from kubo.workers.analyst import AnalystWorker, Sender, render_telegram

_CATALOG_ROOT = Path(__file__).parents[2] / "catalogs"
_TEMPLATES_DIR = _CATALOG_ROOT / "flow_templates"
_PERSONAS_DIR = _CATALOG_ROOT / "personas"

# summary + análise no relatório precisa de folga (R4); o default 1024 truncaria.
_INTEGRATIONS_DIR = _CATALOG_ROOT / "integrations"

# summary + análise no relatório precisa de folga (R4); o default 1024 truncaria.
_REPORT_MAX_TOKENS = 4096
_ANALYST_PERSONA = "analista"
_HUMAN_PERSONA = "humano"
# Estados do board (o handler É template-específico, E4). As transições são validadas contra
# o snapshot congelado — estes nomes só precisam concordar com o template (ambos no mesmo PR).
_CREATED, _ANALYZING, _DELIVERED, _FAILED = "created", "analyzing", "delivered", "failed"
# `analysis-review` acrescenta o gate: a analista PARA em awaiting_review, o humano recebe task,
# a decisão leva as duas a delivered|rejected (ADR-0018 §IV/§V).
_AWAITING, _REJECTED = "awaiting_review", "rejected"
_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FlowRunResult:
    """O que `run_flow` devolve ao chamador (CLI): o flow instanciado, o task da analista, o
    run que o executou e o estado final. Para `analysis` é `delivered`|`failed`; para
    `analysis-review` o caminho feliz para em `awaiting_review` (gate aberto) e `gate_task`
    aponta o task do humano criado na abertura do gate (None quando não há gate)."""

    flow: RecordID
    task: RecordID
    run: RecordID
    state: str
    gate_task: RecordID | None = None


@dataclass(frozen=True)
class FlowBehavior:
    """Binding template→comportamento (E4). `run` é o único obrigatório (o disparo); `resume`/
    `reject` existem só para templates com gate (`analysis-review`). Template novo com gate =
    novas funções + PR = gate humano — nunca registry dinâmico por string (DSL, §I.4)."""

    run: Callable[..., FlowRunResult]
    resume: Callable[..., None] | None = None
    reject: Callable[..., None] | None = None


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
    behavior = _FLOW_REGISTRY.get(template_name)
    if behavior is None:
        raise ConfigError(f"template '{template_name}' sem handler no FLOW_REGISTRY")
    personas = load_personas(_PERSONAS_DIR)
    return behavior.run(
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


def _run_analysis_review(
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
    """Comportamento do `analysis-review` (E4, ADR-0018 §IV/§V): roda a analista em modo
    PRODUCE-ONLY (`destination=None`) — o relatório PARA no gate ANTES do envio (D37). Sucesso
    abre o gate: a analista vai a `awaiting_review`, o humano recebe task (D33) e o dono é
    notificado (best-effort). Falha → `failed`, sem gate. O envio só acontece na aprovação."""
    analista = personas.get(_ANALYST_PERSONA)
    if analista is None or _HUMAN_PERSONA not in personas:
        raise ConfigError(
            f"'analysis-review' exige as personas '{_ANALYST_PERSONA}' e '{_HUMAN_PERSONA}'"
        )
    _assert_permissions(analista, AnalystWorker.manifest)
    resolved_executor = executor if executor is not None else _build_executor(analista)

    inst = instantiate_flow(db, template=template, personas=personas, question=question)
    task = create_task(db, flow=inst.flow, persona=inst.personas[_ANALYST_PERSONA], state=_CREATED)
    transition_task(db, task, from_state=_CREATED, to_state=_ANALYZING)

    worker = AnalystWorker(
        resolved_executor,
        prompt=analista.prompt,
        destination=None,  # produce-only: capacidade-ausente, o gate segura o relatório
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
    if not _run_succeeded(db, run_id):
        transition_task(db, task, from_state=_ANALYZING, to_state=_FAILED)
        return FlowRunResult(flow=inst.flow, task=task, run=run_id, state=_FAILED)

    gate_task = open_gate(
        db,
        analyst_task=task,
        analyst_from=_ANALYZING,
        analyst_to=_AWAITING,
        flow=inst.flow,
        human_persona=inst.personas[_HUMAN_PERSONA],
        gate_state=_AWAITING,
    )
    _notify_gate(
        db,
        flow=inst.flow,
        gate_task=gate_task,
        destination=destination,
        question=question,
        base_url=base_url,
        senders=senders,
    )
    return FlowRunResult(
        flow=inst.flow, task=task, run=run_id, state=_AWAITING, gate_task=gate_task
    )


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


def resume_gate(
    db: Any,
    *,
    gate_task: RecordID,
    destination: ResolvedDestination,
    base_url: str,
    senders: Mapping[str, Sender] | None = None,
) -> None:
    """Aprova um gate pelo browser (ADR-0018 §V): despacha ao comportamento do template do
    flow (E4). Falha de ENVIO propaga e deixa o gate ABERTO (at-least-once — o dono clica de
    novo); a rota da UI é casca que traduz isso em erro visível."""
    behavior = _behavior_for_gate(db, gate_task)
    if behavior.resume is None:
        raise ConfigError("o template deste gate não suporta aprovação")
    behavior.resume(
        db, gate_task=gate_task, destination=destination, base_url=base_url, senders=senders
    )


def reject_gate(db: Any, *, gate_task: RecordID, reason: str) -> None:
    """Rejeita um gate pelo browser COM motivo obrigatório (ADR-0018 §IV): despacha ao
    comportamento do template. Arquiva o flow (as 2 tasks → rejected). Sem envio."""
    behavior = _behavior_for_gate(db, gate_task)
    if behavior.reject is None:
        raise ConfigError("o template deste gate não suporta rejeição")
    behavior.reject(db, gate_task=gate_task, reason=reason)


def _behavior_for_gate(db: Any, gate_task: RecordID) -> FlowBehavior:
    """Resolve o FlowBehavior pelo `template_name` do flow do gate (E4: comportamento keyed
    pelo nome). Gate sem template registrado → ConfigError. A leitura passa pela store
    (`template_of_task`, invariante 2), nunca `db.query` direto aqui."""
    name = template_of_task(db, gate_task)
    behavior = _FLOW_REGISTRY.get(name) if name is not None else None
    if behavior is None:
        raise ConfigError(f"gate sem template no FLOW_REGISTRY: {name!r}")
    return behavior


def _resume_review(
    db: Any,
    *,
    gate_task: RecordID,
    destination: ResolvedDestination,
    base_url: str,
    senders: Mapping[str, Sender] | None,
) -> None:
    """Aprovação do `analysis-review` (ADR-0018 §V): re-hidrata o contexto do grafo, ENVIA o
    relatório (mecânico, sem LLM — a prosa do deliverable + as fontes das arestas `consults`),
    registra o dispatch de report e — SÓ se o envio deu certo — decide o gate (delivered) numa
    transação. Envio falho → dispatch(error) e o gate segue aberto; espelha o `_deliver`."""
    ctx = read_gate_context(db, gate_task)
    if ctx is None:
        raise StateError("gate não resolve um flow/deliverable")
    items = [_distilled_rid(s.id) for s in ctx.sources]
    try:
        text = render_telegram(ctx.content, ctx.sources, base_url)
        _send_telegram(destination, text, senders, parse_mode="HTML")
    except SenderError:
        _dispatch_report(db, destination, items, status="error")
        raise
    _dispatch_report(db, destination, items, status="ok")
    decide_gate(
        db,
        analyst_task=ctx.analyst_task,
        gate_task=ctx.gate_task,
        to_state=_DELIVERED,
        decision="approved",
    )


def _reject_review(db: Any, *, gate_task: RecordID, reason: str) -> None:
    """Rejeição do `analysis-review` (ADR-0018 §IV): arquiva as 2 tasks → rejected com o motivo
    obrigatório. `decide_gate` valida o motivo e a atomicidade (transação)."""
    ctx = read_gate_context(db, gate_task)
    if ctx is None:
        raise StateError("gate não resolve um flow")
    decide_gate(
        db,
        analyst_task=ctx.analyst_task,
        gate_task=ctx.gate_task,
        to_state=_REJECTED,
        decision="rejected",
        reason=reason,
    )


def _notify_gate(
    db: Any,
    *,
    flow: RecordID,
    gate_task: RecordID,
    destination: ResolvedDestination,
    question: str,
    base_url: str,
    senders: Mapping[str, Sender] | None,
) -> None:
    """Avisa o dono que um gate abriu + grava o dispatch de gate (ADR-0018 §III). BEST-EFFORT:
    falha de notificação NÃO falha o gate (loga com flow_id/task_id e segue — o board é a fonte
    da verdade). O dispatch de gate NÃO move o watermark do digest (filtro artifact='digest')."""
    try:
        text = (
            f"🔔 Um relatório aguarda sua aprovação no Kubo.\n\n{question}\n\n"
            f"Abra o board: {base_url}/flows"
        )
        _send_telegram(destination, text, senders, parse_mode=None)
        insert_dispatch(
            db,
            destination=destination.id,
            channel=destination.channel,
            status="ok",
            artifact="gate",
            watermark=None,
            item_count=0,
            items=[],
        )
    except Exception as exc:  # noqa: BLE001 — fronteira best-effort: o gate vive sem notificação
        _log.warning(
            "gate.notify_failed",
            error=type(exc).__name__,
            flow_id=str(flow),
            task_id=str(gate_task),
        )


def _dispatch_report(
    db: Any, destination: ResolvedDestination, items: list[RecordID], *, status: str
) -> None:
    """Grava o dispatch de report da aprovação (artifact=report, watermark None — não move o
    watermark do digest). `items` = as fontes consultadas (auditoria, aparece em Envios)."""
    insert_dispatch(
        db,
        destination=destination.id,
        channel=destination.channel,
        status=status,
        artifact="report",
        watermark=None,
        item_count=len(items),
        items=items,
    )


def _send_telegram(
    destination: ResolvedDestination,
    text: str,
    senders: Mapping[str, Sender] | None,
    *,
    parse_mode: str | None,
) -> None:
    """Envia `text` ao destino Telegram: o sender injetado (default = real) + o token resolvido
    do catálogo de integrações. O RUNTIME resolve o segredo (o worker nunca lê env, §8)."""
    if destination.channel != "telegram":
        raise SenderError(f"canal {destination.channel!r} não suportado")
    sender = (senders or {}).get("telegram") or send_telegram
    sender(token=_telegram_token(), chat_id=destination.address, text=text, parse_mode=parse_mode)


def _telegram_token() -> str:
    """Resolve o token do Telegram do catálogo de integrações (mesmo seam do worker). Token
    ausente → SenderError (falha de ENTREGA, não crash)."""
    resolved = resolve_integrations(["telegram"], load_integrations(_INTEGRATIONS_DIR))
    secret = resolved["telegram"].secret
    if not secret:
        raise SenderError("integração 'telegram' sem segredo resolvido")
    return secret


def _distilled_rid(raw: str) -> RecordID:
    """Forma string `distilled:<key>` (validada na origem: vem das arestas `consults`) → RecordID
    para a auditoria do dispatch."""
    return RecordID("distilled", raw.partition(":")[2])


# Binding template→comportamento HARDCODED (E4). Template novo = FlowBehavior novo + PR (gate
# humano). `resume`/`reject` só existem para templates com gate (`analysis-review`).
_FLOW_REGISTRY: dict[str, FlowBehavior] = {
    "analysis": FlowBehavior(run=_run_analysis),
    "analysis-review": FlowBehavior(
        run=_run_analysis_review, resume=_resume_review, reject=_reject_review
    ),
}
