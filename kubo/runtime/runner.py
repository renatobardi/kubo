"""Runner mínimo de worker sob contrato (ADR-0009).

Fluxo: `validate_worker` → abre `run` → monta o ctx read-only → `run()` → valida
o `RunResult` → persiste cada payload por match EXPLÍCITO tipo→função da store →
fecha o `run` (ok com stats, ou error). O runtime persiste; o worker devolve
dados tipados e nunca toca a store. Exceção do worker é capturada NA FRONTEIRA e
vira erro estruturado no `run` — o runtime não explode.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError
from surrealdb import RecordID

from kubo.contracts.models import (
    DispatchPayload,
    DistilledPayload,
    ErrorInfo,
    ItemPayload,
    Payload,
    ReportPayload,
    RunResult,
    WorkerManifest,
)
from kubo.contracts.worker import validate_worker
from kubo.embedding import Embedder
from kubo.errors import ConfigError, format_validation_error
from kubo.runtime.context import GraphKnowledge, RunContext
from kubo.runtime.integrations import load_integrations, resolve_integrations
from kubo.store.flows import insert_deliverable
from kubo.store.knowledge import (
    Chunk,
    fail_run,
    finish_run,
    get_or_create_entity,
    insert_dispatch,
    insert_distilled,
    start_run,
    upsert_item,
    upsert_source,
)


@dataclass(frozen=True)
class FlowCtx:
    """Contexto de flow opcional passado a `run_worker`: os RecordIDs de flow/task que o
    `_persist` usa para costurar a proveniência de um `ReportPayload` (`produces`/`consults`).

    O worker NUNCA o vê — atribuição de proveniência mora no runtime, não no worker (ADR-0016
    §III). A analista tem LLM no circuito, então a disciplina de ref opaco vale: nem bug nem
    injeção conseguem apontar um relatório para o flow/task errado."""

    flow: RecordID
    task: RecordID


_DEFAULT_CATALOG_DIR = Path(__file__).parents[2] / "catalogs" / "integrations"
# Teto do `message` de erro: o caminho de exceção é por onde conteúdo coletado
# hostil vazaria para run.error/log (str(exc) de parse ecoa o trecho que quebrou).
_MSG_CAP = 500


def _error_message(exc: Exception) -> str:
    """Mensagem da exceção para o ErrorInfo, TRUNCADA e sem vazar input.

    Para ValidationError, `str(exc)` embutiria o input_value (conteúdo coletado /
    payload hostil): usa o formatador seguro da fronteira (só loc+msg). Trunca em
    500 — o caminho de exceção é por onde conteúdo hostil vazaria para run.error/log."""
    text = format_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
    return text[:_MSG_CAP]


def _error_from_exception(exc: Exception) -> ErrorInfo:
    """Exceção capturada na fronteira → ErrorInfo estruturado (não explode).

    `kind` distingue a origem: ConfigError (permissão/segredo na montagem do ctx),
    ValidationError (RunResult malformado = violação de contrato) e o resto
    (falha do próprio `run()`). Todos fecham o run em erro estruturado."""
    if isinstance(exc, ConfigError):
        kind = "config"
    elif isinstance(exc, ValidationError):
        kind = "contract"
    else:
        kind = "worker_exception"
    return ErrorInfo(
        kind=kind,
        message=_error_message(exc),
        detail={"exception": type(exc).__name__},
    )


def _persist(
    db: Any,
    payloads: list[Payload],
    run_id: RecordID,
    knowledge: GraphKnowledge,
    flow_ctx: FlowCtx | None,
) -> int:
    """Persiste cada payload por match EXPLÍCITO e hardcoded tipo→função da store.
    Devolve a contagem de `DistilledPayload` pulados por `ref` não-resolvível.

    Sem registry/plugin de persistência (seria DSL disfarçada, proibido). É por-item
    e idempotente: cada upsert da store já é atômico (ADR-0009 item VII), então falha
    parcial deixa itens gravados e a re-execução cura — não há mega-transação nem retry.
    Para ItemPayload, a source (embutida inline) é upsertada antes; idempotência torna
    a repetição gratuita, e `run_id` grava a proveniência de execução `item -[collected_by]->
    run` (ADR-0008 §VI) — a run já existe quando `_persist` roda, então a aresta ENFORCED é
    segura.

    Para DistilledPayload (ADR-0013 §III): `ref` resolve a RecordID via `knowledge.resolve`
    (§III.2); entidades resolvem por nome via `get_or_create_entity` (§III.4); o resto
    (distilled + chunks + produced_by + mentions) grava atômico dentro de `insert_distilled`
    (§III.8). Um `ref` não-resolvível (bug ou vazamento pro campo, §III.6) PULA só aquele
    payload — nunca levanta — e é contado para o `run_worker` decidir o fechamento do run.
    `insert_distilled` pode levantar (`StoreError`/`ValueError` de dim); essas propagam:
    só o ref não-resolvível é skip-and-continue."""
    unresolved = 0
    for payload in payloads:
        if isinstance(payload, ItemPayload):
            source = upsert_source(
                db,
                kind=payload.source.kind,
                canonical=payload.source.canonical,
                title=payload.source.title,
            )
            upsert_item(
                db,
                source=source,
                external_id=payload.external_id,
                content=payload.content,
                url=payload.url,
                title=payload.title,
                metadata=payload.metadata,
                run=run_id,
            )
        elif isinstance(payload, DistilledPayload):
            item = knowledge.resolve(payload.ref)
            if item is None:
                unresolved += 1
                continue
            entities = [
                get_or_create_entity(db, name=e.name, kind=e.kind) for e in payload.entities
            ]
            chunks = [
                Chunk(
                    text=c.text,
                    seq=c.seq,
                    embedding=c.embedding,
                    model=c.model,
                    dim=c.dim,
                    task_type=c.task_type,
                )
                for c in payload.chunks
            ]
            insert_distilled(
                db, item=item, summary=payload.summary, chunks=chunks, run=run_id, entities=entities
            )
        elif isinstance(payload, DispatchPayload):
            # `items` (strings validadas na borda pydantic) → RecordID para a store.
            # `run_id` NÃO entra: dispatch é fato de entrega, não tem aresta produced_by
            # (sem consumidor — ADR-0015 §II); a proveniência de execução é o próprio run.
            insert_dispatch(
                db,
                destination=payload.destination,
                channel=payload.channel,
                status=payload.status,
                artifact=payload.artifact,
                watermark=payload.watermark,
                item_count=payload.item_count,
                items=[_parse_distilled_id(s) for s in payload.items],
                error=payload.error.model_dump() if payload.error else None,
            )
        elif isinstance(payload, ReportPayload):
            # Costura de proveniência via `flow_ctx` (ADR-0016 §III): o worker não conhece
            # os RecordIDs de flow/task. `consulted` (strings validadas na borda) vem do
            # RETRIEVAL, nunca do LLM (§VI). ReportPayload sem flow_ctx é erro de config do
            # chamador (só o flow runner emite report, sempre com ctx) → fecha o run em
            # erro estruturado kind="config", nunca crash.
            if flow_ctx is None:
                raise ConfigError("ReportPayload exige flow_ctx (proveniência de flow/task)")
            insert_deliverable(
                db,
                flow=flow_ctx.flow,
                task=flow_ctx.task,
                kind="report",
                content=payload.content,
                consulted=[_parse_distilled_id(s) for s in payload.consulted],
            )
        else:  # SourcePayload — o único outro membro restante da união
            upsert_source(db, kind=payload.kind, canonical=payload.canonical, title=payload.title)
    return unresolved


def _parse_distilled_id(raw: str) -> RecordID:
    """Converte a forma string `distilled:<id>` (validada na borda pydantic) em RecordID.

    Não revalida o formato: `DispatchPayload._items_are_distilled_ids` já é a fronteira
    (os payloads chegam ao `_persist` já validados por `RunResult.model_validate`)."""
    table, _, key = raw.partition(":")
    return RecordID(table, key)


def _build_context(
    manifest: WorkerManifest,
    config: dict[str, Any] | None,
    catalog_dir: Path,
    run_id: RecordID,
    db: Any,
    embedder: Embedder | None,
) -> RunContext:
    """Monta o ctx read-only: config validada contra o schema do manifest,
    integrações resolvidas (declaradas ∩ existentes; segredo pelo runtime),
    o adaptador de conhecimento (`GraphKnowledge`, ADR-0013 §III) e logger
    bound com run_id/worker."""
    config_model = manifest.config.model_validate(config or {})
    catalog = load_integrations(catalog_dir)
    integrations = resolve_integrations(manifest.integrations, catalog)
    logger = structlog.get_logger().bind(run_id=str(run_id), worker=manifest.name)
    return RunContext(
        config=config_model,
        integrations=integrations,
        knowledge=GraphKnowledge(db),
        logger=logger,
        embedder=embedder,
    )


def run_worker(
    db: Any,
    worker: object,
    *,
    config: dict[str, Any] | None = None,
    catalog_dir: Path = _DEFAULT_CATALOG_DIR,
    embedder: Embedder | None = None,
    flow_ctx: FlowCtx | None = None,
) -> RecordID:
    """Executa um worker sob contrato ponta a ponta e devolve o id do `run`.

    `validate_worker` roda ANTES de abrir o run (worker inválido nunca executa —
    levanta ContractError, sem run órfão). A partir daí, toda exceção vira erro
    estruturado no run. Payloads e error podem coexistir (ADR-0009 item VII): os
    payloads entregues são persistidos e SÓ DEPOIS o run fecha em erro.

    `flow_ctx` (opcional) carrega os RecordIDs de flow/task para o `_persist` costurar a
    proveniência de um `ReportPayload` (ADR-0016 §III) — o mecanismo genérico só ganha um
    contexto de ATRIBUIÇÃO opcional, não lógica de flow. `None` para os workers da fase 1."""
    manifest = validate_worker(worker)
    run_id = start_run(db, worker=manifest.name)
    try:
        ctx = _build_context(manifest, config, catalog_dir, run_id, db, embedder)
        raw_result = worker.run(ctx)  # type: ignore[attr-defined]  # assinatura validada acima
        result = RunResult.model_validate(raw_result)
        # _persist DENTRO do try: uma falha de store não pode deixar o run travado
        # em 'running' nem propagar exceção crua fora da fronteira.
        unresolved = _persist(db, result.payloads, run_id, ctx.knowledge, flow_ctx)
        # Precedência do fechamento: erro do próprio worker vence (já é o motivo de
        # falha mais específico); senão, ref não-resolvível (defensivo, §III.6);
        # senão, ok.
        if result.error is not None:
            fail_run(db, run_id, error=result.error.model_dump())
        elif unresolved > 0:
            fail_run(
                db,
                run_id,
                error=ErrorInfo(
                    kind="unresolvable_ref",
                    message=f"{unresolved} payload(s) com ref não-resolvível",
                    detail={"count": unresolved},
                ).model_dump(),
            )
        else:
            finish_run(db, run_id, stats=result.stats.model_dump())
    except Exception as exc:  # noqa: BLE001 — fronteira: exceção vira erro estruturado, não crash
        fail_run(db, run_id, error=_error_from_exception(exc).model_dump())
    return run_id
