"""Job de ingestão de Material em background (KUBO-202, ADR-0049 §III).

O upload cria o Material `pending` (arquivo gravado, zero LLM no request).
Este job roda a cada 5 min no scheduler, consome `pending` e faz:
parse → sumário (persona `summarizer`) → sectionizer (persona `sectionizer`)
→ marca `ready` (ou `failed` com motivo).

Retomada após restart é consequência de o estado viver no banco, não em memória
(invariante 7: sem orquestrador pesado). Falha de um material não derruba os
demais — isolamento por material, como o sweep de coleta.
"""

from __future__ import annotations

from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import ConfigError, ExecutorError, MaterialParseError, StoreError
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.personas import resolve_persona
from kubo.store import study as study_store
from kubo.study.config import DEFAULT_MODEL as _DEFAULT_MODEL
from kubo.study.config import SUMMARY_MAX_TOKENS as _SUMMARY_MAX_TOKENS
from kubo.study.parsing import MaterialFormat, ParsedMaterial, parse_material
from kubo.study.sectionizer import sectionize
from kubo.study.summarizer import Summarizer

_log = structlog.get_logger(__name__)


def _as_fmt(value: str) -> MaterialFormat:
    """Coerce `str` do banco para `MaterialFormat` (Literal). O upload só aceita epub/pdf."""
    if value not in ("epub", "pdf"):
        raise MaterialParseError(f"formato desconhecido: {value!r}")
    return value  # type: ignore[return-value]  # validado acima


# Pinos de LLM compartilhados com routes/study.py (kubo.study.config).
_SECTIONIZER_MAX_TOKENS = 8192
_LLM_TIMEOUT = 30.0


# --- seams para testes (monkeypatcháveis sem tocar no banco/API real) -------------------


def _list_pending(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[study_store.Material]:
    return study_store.list_pending_materials(db, tenant_id=tenant_id, user_id=user_id)


def _parse_material(fmt: MaterialFormat, path: str) -> ParsedMaterial:
    """Lê o arquivo do volume e parseia. Levanta `MaterialParseError` se inválido."""
    from pathlib import Path

    data = Path(path).read_bytes()
    return parse_material(data, fmt)


def _build_summarizer(db: Any, tenant_id: RecordID, user_id: RecordID) -> Summarizer:
    persona = resolve_persona(db, tenant_id, user_id, "summarizer")
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or _DEFAULT_MODEL,
            max_tokens=_SUMMARY_MAX_TOKENS,
            timeout=_LLM_TIMEOUT,
        ),
        max_attempts=1,
    )
    return Summarizer(executor=executor, prompt=persona.prompt)


def _build_sectionizer(db: Any, tenant_id: RecordID, user_id: RecordID) -> tuple[ApiExecutor, str]:
    persona = resolve_persona(db, tenant_id, user_id, "sectionizer")
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or _DEFAULT_MODEL,
            max_tokens=_SECTIONIZER_MAX_TOKENS,
            timeout=_LLM_TIMEOUT,
        ),
        max_attempts=1,
    )
    return executor, persona.prompt


def _ingest_material(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    material_id: RecordID,
    chapters: Any,
    sections: Any,
    summary: str | None,
) -> study_store.Material:
    return study_store.ingest_material(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material_id,
        chapters=chapters,
        sections=sections,
        summary=summary,
    )


def _mark_failed(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    material_id: RecordID,
    error: str,
) -> study_store.Material:
    return study_store.mark_material_failed(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        material_id=material_id,
        error=error,
    )


# --- job --------------------------------------------------------------------------------


def execute_study_ingest_job(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> None:
    """Consome materiais `pending`: parse → sumário → sections → ready (ou failed).

    Isolamento por material: falha de um não derruba os demais. Setup de
    summarizer/sectionizer é feito uma vez por execução (não por material) —
    se falhar, todos os pending viram `failed` com o motivo de setup.
    """
    pending = _list_pending(db, tenant_id=tenant_id, user_id=user_id)
    if not pending:
        return

    # Setup de personas (uma vez por execução). Falha aqui = todos pending falham.
    try:
        summarizer = _build_summarizer(db, tenant_id, user_id)
    except (ConfigError, StoreError) as exc:
        _log.warning("study.ingest.summarizer_setup_failed", worker="study_ingest", exc_info=True)
        for material in pending:
            _mark_failed(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                material_id=material.id,
                error=f"summarizer indisponível: {exc}",
            )
        return

    sectionizer_executor: tuple[ApiExecutor, str] | None
    try:
        sectionizer_executor = _build_sectionizer(db, tenant_id, user_id)
    except (ConfigError, StoreError):
        _log.warning("study.ingest.sectionizer_setup_failed", worker="study_ingest", exc_info=True)
        sectionizer_executor = None  # fallback: 1 section = 1 capítulo

    ingested = 0
    failed = 0
    for material in pending:
        try:
            parsed = _parse_material(_as_fmt(material.fmt), material.file_path)
            summary = summarizer.generate(parsed)
            sections_map = None
            if sectionizer_executor is not None:
                executor, prompt = sectionizer_executor
                sections_map = sectionize(
                    executor=executor, prompt=prompt, chapters=parsed.chapters
                )
            _ingest_material(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                material_id=material.id,
                chapters=parsed.chapters,
                sections=sections_map,
                summary=summary,
            )
            ingested += 1
        except MaterialParseError as exc:
            failed += 1
            _log.warning(
                "study.ingest.parse_failed",
                worker="study_ingest",
                material=str(material.id),
                exc_info=True,
            )
            _mark_failed(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                material_id=material.id,
                error=f"parse: {exc}",
            )
        except ExecutorError as exc:
            failed += 1
            _log.warning(
                "study.ingest.llm_failed",
                worker="study_ingest",
                material=str(material.id),
                exc_info=True,
            )
            _mark_failed(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                material_id=material.id,
                error=f"LLM: {exc}",
            )
        except Exception:  # noqa: BLE001 — isola o material: loga e segue
            failed += 1
            _log.exception(
                "study.ingest.unexpected", worker="study_ingest", material=str(material.id)
            )
            _mark_failed(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                material_id=material.id,
                error="erro inesperado na ingestão",
            )
    _log.info(
        "study.ingest.done",
        worker="study_ingest",
        total=len(pending),
        ingested=ingested,
        failed=failed,
    )
