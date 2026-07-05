"""Runner mínimo de worker sob contrato (ADR-0009).

Fluxo: `validate_worker` → abre `run` → monta o ctx read-only → `run()` → valida
o `RunResult` → persiste cada payload por match EXPLÍCITO tipo→função da store →
fecha o `run` (ok com stats, ou error). O runtime persiste; o worker devolve
dados tipados e nunca toca a store. Exceção do worker é capturada NA FRONTEIRA e
vira erro estruturado no `run` — o runtime não explode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError
from surrealdb import RecordID

from kubo.contracts.models import ErrorInfo, ItemPayload, Payload, RunResult, WorkerManifest
from kubo.contracts.worker import validate_worker
from kubo.errors import ConfigError
from kubo.runtime.context import EmptyKnowledge, RunContext
from kubo.runtime.integrations import load_integrations, resolve_integrations
from kubo.store.knowledge import fail_run, finish_run, start_run, upsert_item, upsert_source

_DEFAULT_CATALOG_DIR = Path(__file__).parents[2] / "catalogs" / "integrations"
# Teto do `message` de erro: o caminho de exceção é por onde conteúdo coletado
# hostil vazaria para run.error/log (str(exc) de parse ecoa o trecho que quebrou).
_MSG_CAP = 500


def _error_message(exc: Exception) -> str:
    """Mensagem da exceção para o ErrorInfo, TRUNCADA e sem vazar input.

    Para ValidationError, `str(exc)` embutiria o input_value (conteúdo coletado /
    payload hostil); usa `errors(include_input=False)`. Depois trunca em 500: o
    caminho de exceção é por onde conteúdo hostil vazaria para run.error/log."""
    if isinstance(exc, ValidationError):
        # Lê SÓ loc+msg, nunca e['input'] — o input carregaria o payload hostil.
        text = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
    else:
        text = str(exc)
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


def _persist(db: Any, payloads: list[Payload]) -> None:
    """Persiste cada payload por match EXPLÍCITO e hardcoded tipo→função da store.

    Sem registry/plugin de persistência (seria DSL disfarçada, proibido). É por-item
    e idempotente: cada upsert da store já é atômico (ADR-0009 item VII), então falha
    parcial deixa itens gravados e a re-execução cura — não há mega-transação nem retry.
    Para ItemPayload, a source (embutida inline) é upsertada antes; idempotência torna
    a repetição gratuita. Membro novo da união (distilled, M6) força tratamento aqui:
    o pyright acusa o acesso a campo inexistente no ramo `else`, não passa em silêncio."""
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
            )
        else:  # SourcePayload — o único outro membro da união hoje
            upsert_source(db, kind=payload.kind, canonical=payload.canonical, title=payload.title)


def _build_context(
    manifest: WorkerManifest,
    config: dict[str, Any] | None,
    catalog_dir: Path,
    run_id: RecordID,
) -> RunContext:
    """Monta o ctx read-only: config validada contra o schema do manifest,
    integrações resolvidas (declaradas ∩ existentes; segredo pelo runtime),
    seam de conhecimento vazio e logger bound com run_id/worker."""
    config_model = manifest.config.model_validate(config or {})
    catalog = load_integrations(catalog_dir)
    integrations = resolve_integrations(manifest.integrations, catalog)
    logger = structlog.get_logger().bind(run_id=str(run_id), worker=manifest.name)
    return RunContext(
        config=config_model,
        integrations=integrations,
        knowledge=EmptyKnowledge(),
        logger=logger,
    )


def run_worker(
    db: Any,
    worker: object,
    *,
    config: dict[str, Any] | None = None,
    catalog_dir: Path = _DEFAULT_CATALOG_DIR,
) -> RecordID:
    """Executa um worker sob contrato ponta a ponta e devolve o id do `run`.

    `validate_worker` roda ANTES de abrir o run (worker inválido nunca executa —
    levanta ContractError, sem run órfão). A partir daí, toda exceção vira erro
    estruturado no run. Payloads e error podem coexistir (ADR-0009 item VII): os
    payloads entregues são persistidos e SÓ DEPOIS o run fecha em erro."""
    manifest = validate_worker(worker)
    run_id = start_run(db, worker=manifest.name)
    try:
        ctx = _build_context(manifest, config, catalog_dir, run_id)
        raw_result = worker.run(ctx)  # type: ignore[attr-defined]  # assinatura validada acima
        result = RunResult.model_validate(raw_result)
        # _persist DENTRO do try: uma falha de store não pode deixar o run travado
        # em 'running' nem propagar exceção crua fora da fronteira.
        _persist(db, result.payloads)
        if result.error is not None:
            fail_run(db, run_id, error=result.error.model_dump())
        else:
            finish_run(db, run_id, stats=result.stats.model_dump())
    except Exception as exc:  # noqa: BLE001 — fronteira: exceção vira erro estruturado, não crash
        fail_run(db, run_id, error=_error_from_exception(exc).model_dump())
    return run_id
