"""KUBO-202 — Job de ingestão de Material em background (ADR-0049 §III).

Testes unitários com store e executors mockados. O job consome materiais
`pending`, faz parse + sumário + sectionizer e marca `ready` ou `failed`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.scheduler import study_ingest
from kubo.store.study import Material
from kubo.study.parsing import ParsedChapter, ParsedMaterial

_TENANT = RecordID("tenant", "t1")
_USER = RecordID("user", "u1")
_TOPIC = RecordID("topic", "to1")
_MATERIAL = RecordID("material", "m1")


def _pending_material() -> Material:
    return Material(
        id=_MATERIAL,
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC,
        title="Livro",
        fmt="epub",
        original_filename="livro.epub",
        file_path="/data/livro.epub",
        size_bytes=1024,
        chapter_count=0,
        summary=None,
        created_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        status="pending",
        error=None,
        ingested_at=None,
    )


def _parsed() -> ParsedMaterial:
    return ParsedMaterial(
        title="Livro",
        chapters=[ParsedChapter(seq=1, title="Cap 1", content="Conteúdo.", part=None)],
    )


class _FakeSummarizer:
    def generate(self, parsed: ParsedMaterial) -> str:
        return "Resumo do livro."


class _FakeSectionizer:
    """Devolve (executor, prompt) que o sectionize usa."""

    def __init__(self) -> None:
        self.called = False

    def __call__(self, db: Any, tenant_id: RecordID, user_id: RecordID) -> tuple[Any, str]:
        self.called = True
        return _FakeExecutor(), "prompt"


class _FakeExecutor:
    """Executor que o sectionize chama — devolve SectionizerOutput com 1 seção fallback."""

    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
        from kubo.study.sectionizer import SectionItem, SectionizerOutput

        # Devolve 1 seção = conteúdo inteiro (espelha fallback_part).
        return SectionizerOutput(
            sections=[
                SectionItem(
                    title="Seção",
                    content="Conteúdo.",
                    summary="Resumo da seção.",
                )
            ]
        )

    def stream_chat(self, **kw: Any):  # type: ignore[no-untyped-def]
        yield ""


@pytest.fixture(autouse=True)
def stub_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stuba store + executors para o job de ingestão."""
    monkeypatch.setattr(study_ingest, "_list_pending", lambda db, **kw: [_pending_material()])
    monkeypatch.setattr(study_ingest, "_parse_material", lambda fmt, path: _parsed())
    monkeypatch.setattr(
        study_ingest, "_build_summarizer", lambda db, tenant_id, user_id: _FakeSummarizer()
    )
    monkeypatch.setattr(
        study_ingest,
        "_build_sectionizer",
        lambda db, tenant_id, user_id: (_FakeExecutor(), "prompt"),
    )
    monkeypatch.setattr(study_ingest, "_ingest_material", lambda db, **kw: _pending_material())
    monkeypatch.setattr(study_ingest, "_mark_failed", lambda db, **kw: _pending_material())


def test_ingest_job_processes_pending_and_marks_ready() -> None:
    """Job lista pending, processa e marca ready."""
    calls: list[dict[str, Any]] = []
    study_ingest._ingest_material = lambda db, **kw: calls.append(kw) or _pending_material()  # type: ignore[method-assign]
    study_ingest.execute_study_ingest_job(db=object(), tenant_id=_TENANT, user_id=_USER)
    assert len(calls) == 1
    assert calls[0]["material_id"] == _MATERIAL


def test_ingest_job_marks_failed_on_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse falha → marca failed com motivo."""
    from kubo.errors import MaterialParseError

    monkeypatch.setattr(
        study_ingest,
        "_parse_material",
        lambda fmt, path: (_ for _ in ()).throw(MaterialParseError("bad epub")),
    )
    failed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        study_ingest, "_mark_failed", lambda db, **kw: failed.append(kw) or _pending_material()
    )
    study_ingest.execute_study_ingest_job(db=object(), tenant_id=_TENANT, user_id=_USER)
    assert len(failed) == 1
    assert "bad epub" in failed[0]["error"]


def test_ingest_job_marks_failed_on_summarizer_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summarizer falha → marca failed."""
    from kubo.errors import ExecutorError

    class _BoomSummarizer:
        def generate(self, parsed: ParsedMaterial) -> str:
            raise ExecutorError("LLM down")

    monkeypatch.setattr(study_ingest, "_build_summarizer", lambda db, t, u: _BoomSummarizer())
    failed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        study_ingest, "_mark_failed", lambda db, **kw: failed.append(kw) or _pending_material()
    )
    study_ingest.execute_study_ingest_job(db=object(), tenant_id=_TENANT, user_id=_USER)
    assert len(failed) == 1


def test_ingest_job_skips_when_no_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem pending, job é no-op."""
    monkeypatch.setattr(study_ingest, "_list_pending", lambda db, **kw: [])
    ingest_calls: list[Any] = []
    monkeypatch.setattr(study_ingest, "_ingest_material", lambda db, **kw: ingest_calls.append(kw))
    study_ingest.execute_study_ingest_job(db=object(), tenant_id=_TENANT, user_id=_USER)
    assert ingest_calls == []


def test_ingest_job_isolates_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha de um material não derruba os demais."""
    from kubo.errors import MaterialParseError

    m1 = _pending_material()
    m2 = Material(
        id=RecordID("material", "m2"),
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC,
        title="Livro 2",
        fmt="epub",
        original_filename="livro2.epub",
        file_path="/data/livro2.epub",
        size_bytes=1024,
        chapter_count=0,
        summary=None,
        created_at=datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc),
        status="pending",
        error=None,
        ingested_at=None,
    )
    monkeypatch.setattr(study_ingest, "_list_pending", lambda db, **kw: [m1, m2])

    call_count = {"parse": 0}

    def _parse(fmt: str, path: str) -> ParsedMaterial:
        call_count["parse"] += 1
        if call_count["parse"] == 1:
            raise MaterialParseError("bad epub 1")
        return _parsed()

    monkeypatch.setattr(study_ingest, "_parse_material", _parse)
    failed: list[dict[str, Any]] = []
    ingested: list[dict[str, Any]] = []
    monkeypatch.setattr(study_ingest, "_mark_failed", lambda db, **kw: failed.append(kw) or m1)
    monkeypatch.setattr(
        study_ingest, "_ingest_material", lambda db, **kw: ingested.append(kw) or m2
    )
    study_ingest.execute_study_ingest_job(db=object(), tenant_id=_TENANT, user_id=_USER)
    assert len(failed) == 1
    assert len(ingested) == 1
