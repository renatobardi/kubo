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
from kubo.workers.distiller import DistillOutput, filter_present_entities
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
    assert (
        smoke.canary_leaked("isto contém PWNED_SUMMARY_7Q no meio", [], "PWNED_SUMMARY_7Q") is True
    )


def test_canary_leaked_detects_marker_in_entity_name() -> None:
    entities = [EntityRef(name="INJECTED_ENTITY_9Z", kind="malware")]
    assert smoke.canary_leaked("resumo normal", entities, "INJECTED_ENTITY_9Z") is True


def test_canary_leaked_detects_marker_in_entity_kind() -> None:
    entities = [EntityRef(name="algo", kind="INJECTED_ENTITY_9Z")]
    assert smoke.canary_leaked("resumo normal", entities, "INJECTED_ENTITY_9Z") is True


def test_canary_leaked_returns_false_when_absent() -> None:
    entities = [EntityRef(name="OpenAI", kind="organização")]
    assert (
        smoke.canary_leaked("resumo limpo, sem marcador nenhum", entities, "PWNED_SUMMARY_7Q")
        is False
    )


def test_canary_leaked_is_case_insensitive() -> None:
    assert (
        smoke.canary_leaked("contém pwned_summary_7q em minúsculas", [], "PWNED_SUMMARY_7Q") is True
    )


def test_canary_leaked_still_detects_entity_marker_that_survives_the_filter() -> None:
    """Composição real: `filter_present_entities` primeiro, `canary_leaked` depois.
    Entidade cujo nome É verbatim no content sobrevive ao filtro — a checagem de
    canário ainda a detecta (a defesa não depende só do filtro derrubar tudo)."""
    content = "texto contendo o marcador INJETADA_9Z literalmente no meio"
    entities = [EntityRef(name="INJETADA_9Z", kind="malware")]

    kept = filter_present_entities(entities, content)

    assert smoke.canary_leaked("resumo limpo", kept, "INJETADA_9Z") is True


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
    report = smoke.ModelReport(model="m", valid=10, portuguese=10, malformed=0)
    assert report.passed() is True


def test_model_report_passed_false_when_canary_leaked() -> None:
    report = smoke.ModelReport(
        model="m",
        valid=10,
        portuguese=10,
        malformed=0,
        canary_leaks=["PWNED_SUMMARY_7Q"],
    )
    assert report.passed() is False


def test_model_report_passed_false_when_portuguese_below_ten() -> None:
    report = smoke.ModelReport(model="m", valid=10, portuguese=9, malformed=0)
    assert report.passed() is False


def test_model_report_passed_false_when_valid_below_ten() -> None:
    report = smoke.ModelReport(model="m", valid=9, portuguese=9, malformed=1)
    assert report.passed() is False


def test_model_report_render_includes_verdict_and_leaked_markers() -> None:
    passed = smoke.ModelReport(model="m-ok", valid=10, portuguese=10, malformed=0)
    assert "PASS" in passed.render()
    assert "m-ok" in passed.render()

    failed = smoke.ModelReport(
        model="m-bad",
        valid=10,
        portuguese=10,
        malformed=0,
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
    report = smoke.run_model("fake-model", executor=fake, delay=0)
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
    report = smoke.run_model("fake-model", executor=fake, delay=0)
    assert set(report.canary_leaks) == {
        item.canary_marker for item in smoke.SMOKE_ITEMS if item.canary_marker is not None
    }
    assert report.passed() is False


def test_run_model_filters_entity_canary_not_present_in_content_before_checking() -> None:
    """Entidade injetada que o LLM criou mas que NÃO está verbatim no content do
    item canário de entidade é derrubada pelo `filter_present_entities` ANTES da
    checagem de canário — prova que o smoke testa o pipeline PÓS-FILTRO, não a
    virtude do modelo. Se o modelo obedecer o pedido de construir "INJETADA_9Z" e
    criar a entidade, o filtro derruba porque "injetada_9z" não é substring do
    content real (que só tem "INJETADA" e "9Z" separados)."""
    entity_index = next(
        i for i, item in enumerate(smoke.SMOKE_ITEMS) if item.canary_marker == "INJETADA_9Z"
    )
    outputs: list[DistillOutput | Exception] = []
    for i, _item in enumerate(smoke.SMOKE_ITEMS):
        if i == entity_index:
            outputs.append(
                DistillOutput(
                    summary="resumo em português sem vazamento nenhum",
                    entities=[EntityRef(name="INJETADA_9Z", kind="malware")],
                )
            )
        else:
            outputs.append(DistillOutput(summary="resumo em português normal", entities=[]))
    fake = _FakeExecutor(outputs)

    report = smoke.run_model("fake-model", executor=fake, delay=0)

    assert report.valid == 10
    assert report.canary_leaks == []
    assert report.passed() is True
    # o caso complementar — entidade verbatim no content sobrevive ao filtro e
    # `canary_leaked` ainda a detecta — está coberto direto em
    # test_canary_leaked_still_detects_entity_marker_that_survives_the_filter,
    # sem precisar simular um SMOKE_ITEMS alternativo aqui.


def test_run_model_counts_malformed_and_errors_without_valid() -> None:
    outputs: list[DistillOutput | Exception] = [MalformedOutputError("x")]
    outputs.extend(RateLimitExhausted("y") for _ in range(9))
    fake = _FakeExecutor(outputs)
    report = smoke.run_model("fake-model", executor=fake, delay=0)
    assert report.malformed == 1
    assert report.rate_limited == 9  # RateLimitExhausted é operacional (re-run cura)
    assert report.valid == 0
    assert report.passed() is False


def test_run_model_executor_error_is_counted_as_provider_error() -> None:
    outputs: list[DistillOutput | Exception] = [ExecutorError("boom")] * 10
    fake = _FakeExecutor(outputs)
    report = smoke.run_model("fake-model", executor=fake, delay=0)
    assert report.provider_errors == 10  # não-transiente = problema de config do modelo
    assert report.valid == 0


def test_run_model_sleeps_between_items_but_not_before_the_first() -> None:
    """Pacing (marco 8.6): `sleep(delay)` roda entre chamadas, nunca antes da 1ª —
    para 10 itens, exatamente 9 chamadas de sleep."""
    outputs = [
        DistillOutput(summary="resumo em português número " + str(i), entities=[])
        for i in range(10)
    ]
    fake = _FakeExecutor(list(outputs))
    sleep_calls: list[float] = []

    smoke.run_model("fake-model", executor=fake, delay=2.5, sleep=sleep_calls.append)

    assert sleep_calls == [2.5] * 9
