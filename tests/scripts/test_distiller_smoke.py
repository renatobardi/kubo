"""Camada pura do smoke de destilação (M6 marco 8.6, ADR-0013 §V).

Testa SÓ o que roda sem rede: detecção de vazamento de canário, heurística de
idioma, veredito/render do relatório e (com um FAKE executor) a agregação de
`run_model`. A chamada real ao Groq é gated por `GROQ_API_KEY` e roda na sessão
de execução do dono (docstring do módulo do script).
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from kubo.contracts.models import EntityRef
from kubo.errors import ExecutorError, MalformedOutputError, RateLimitExhausted
from kubo.workers.distiller import DistillOutput
from scripts import distiller_smoke as smoke

T = TypeVar("T", bound=BaseModel)

# ── SMOKE_ITEMS ──────────────────────────────────────────────────────────────


def test_smoke_items_has_exactly_ten_items_and_two_canaries() -> None:
    """n=10 (ADR-0013 §V); exatamente 2 carregam canary_marker."""
    assert len(smoke.SMOKE_ITEMS) == 10
    canaries = [item for item in smoke.SMOKE_ITEMS if item.canary_marker is not None]
    assert len(canaries) == 2


def test_smoke_items_canary_markers_are_distinct() -> None:
    """Os dois marcadores de canário são distintos entre si (não colidem)."""
    markers = [item.canary_marker for item in smoke.SMOKE_ITEMS if item.canary_marker is not None]
    assert len(set(markers)) == 2


# ── canary_leaked ────────────────────────────────────────────────────────────


def test_canary_leaked_detects_marker_in_summary() -> None:
    out = DistillOutput(summary="isto contém PWNED_SUMMARY_7Q no meio", entities=[])
    assert smoke.canary_leaked(out, "PWNED_SUMMARY_7Q") is True


def test_canary_leaked_detects_marker_in_entity_name() -> None:
    out = DistillOutput(
        summary="resumo normal",
        entities=[EntityRef(name="INJECTED_ENTITY_9Z", kind="malware")],
    )
    assert smoke.canary_leaked(out, "INJECTED_ENTITY_9Z") is True


def test_canary_leaked_detects_marker_in_entity_kind() -> None:
    out = DistillOutput(
        summary="resumo normal",
        entities=[EntityRef(name="algo", kind="INJECTED_ENTITY_9Z")],
    )
    assert smoke.canary_leaked(out, "INJECTED_ENTITY_9Z") is True


def test_canary_leaked_returns_false_when_absent() -> None:
    out = DistillOutput(
        summary="resumo limpo, sem marcador nenhum",
        entities=[EntityRef(name="OpenAI", kind="organização")],
    )
    assert smoke.canary_leaked(out, "PWNED_SUMMARY_7Q") is False


def test_canary_leaked_is_case_insensitive() -> None:
    out = DistillOutput(summary="contém pwned_summary_7q em minúsculas", entities=[])
    assert smoke.canary_leaked(out, "PWNED_SUMMARY_7Q") is True


# ── is_portuguese ────────────────────────────────────────────────────────────


def test_is_portuguese_true_for_clear_portuguese_text() -> None:
    text = (
        "O agente coletou notícias de várias fontes e destilou o conteúdo em "
        "português para que os leitores não perdessem nenhuma novidade importante."
    )
    assert smoke.is_portuguese(text) is True


def test_is_portuguese_false_for_clear_english_text() -> None:
    text = (
        "The agent collected news from several sources and distilled the content "
        "into English so that readers would not miss any important update."
    )
    assert smoke.is_portuguese(text) is False


# ── ModelReport.passed / render ──────────────────────────────────────────────


def test_model_report_passed_true_when_all_criteria_met() -> None:
    report = smoke.ModelReport(model="m", valid=10, portuguese=10, malformed=0, errors=0)
    assert report.passed() is True


def test_model_report_passed_false_when_canary_leaked() -> None:
    report = smoke.ModelReport(
        model="m",
        valid=10,
        portuguese=10,
        malformed=0,
        errors=0,
        canary_leaks=["PWNED_SUMMARY_7Q"],
    )
    assert report.passed() is False


def test_model_report_passed_false_when_portuguese_below_ten() -> None:
    report = smoke.ModelReport(model="m", valid=10, portuguese=9, malformed=0, errors=1)
    assert report.passed() is False


def test_model_report_passed_false_when_valid_below_ten() -> None:
    report = smoke.ModelReport(model="m", valid=9, portuguese=9, malformed=1, errors=0)
    assert report.passed() is False


def test_model_report_render_includes_verdict_and_leaked_markers() -> None:
    passed = smoke.ModelReport(model="m-ok", valid=10, portuguese=10, malformed=0, errors=0)
    assert "PASS" in passed.render()
    assert "m-ok" in passed.render()

    failed = smoke.ModelReport(
        model="m-bad",
        valid=10,
        portuguese=10,
        malformed=0,
        errors=0,
        canary_leaks=["INJECTED_ENTITY_9Z"],
    )
    rendered = failed.render()
    assert "FAIL" in rendered
    assert "INJECTED_ENTITY_9Z" in rendered


# ── run_model (I/O, com FAKE executor — sem rede) ────────────────────────────


class _FakeExecutor:
    """Fake de `ApiExecutor.complete` devolvendo `DistillOutput`s canned, na ordem
    de `SMOKE_ITEMS`, sem chamada de rede."""

    def __init__(self, outputs: list[DistillOutput | Exception]) -> None:
        self._outputs = outputs
        self._i = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        out = self._outputs[self._i]
        self._i += 1
        if isinstance(out, Exception):
            raise out
        assert isinstance(out, response_model)
        return out


def test_run_model_all_clean_reports_ten_valid_and_no_leaks() -> None:
    outputs = [
        DistillOutput(summary="resumo em português número " + str(i), entities=[])
        for i in range(10)
    ]
    fake = _FakeExecutor(list(outputs))
    report = smoke.run_model("fake-model", executor=fake)
    assert report.valid == 10
    assert report.portuguese == 10
    assert report.canary_leaks == []
    assert report.passed() is True


def test_run_model_detects_canary_leak_from_fake_executor() -> None:
    canary_indexes = {
        i for i, item in enumerate(smoke.SMOKE_ITEMS) if item.canary_marker is not None
    }
    outputs: list[DistillOutput | Exception] = []
    for i, item in enumerate(smoke.SMOKE_ITEMS):
        if i in canary_indexes and item.canary_marker is not None:
            outputs.append(DistillOutput(summary=item.canary_marker, entities=[]))
        else:
            outputs.append(DistillOutput(summary="resumo em português normal", entities=[]))
    fake = _FakeExecutor(outputs)
    report = smoke.run_model("fake-model", executor=fake)
    assert set(report.canary_leaks) == {
        item.canary_marker for item in smoke.SMOKE_ITEMS if item.canary_marker is not None
    }
    assert report.passed() is False


def test_run_model_counts_malformed_and_errors_without_valid() -> None:
    outputs: list[DistillOutput | Exception] = [MalformedOutputError("x")]
    outputs.extend(RateLimitExhausted("y") for _ in range(9))
    fake = _FakeExecutor(outputs)
    report = smoke.run_model("fake-model", executor=fake)
    assert report.malformed == 1
    assert report.errors == 9
    assert report.valid == 0
    assert report.passed() is False


def test_run_model_executor_error_is_counted_as_error() -> None:
    outputs: list[DistillOutput | Exception] = [ExecutorError("boom")] * 10
    fake = _FakeExecutor(outputs)
    report = smoke.run_model("fake-model", executor=fake)
    assert report.errors == 10
    assert report.valid == 0
