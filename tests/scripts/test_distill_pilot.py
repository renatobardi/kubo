"""Camada pura do piloto lado a lado do dreno (sessão 0014, gate B2).

Testa `run_candidate` (com executor fake, sem rede) e `render_pilot` — mesmo
desenho de tests/scripts/test_distiller_smoke.py. A casca de I/O (seleção da
amostra da store, escrita do doc) é exercida na sessão de execução contra o banco.
"""

from __future__ import annotations

from typing import TypeVar, cast

from pydantic import BaseModel

from kubo.contracts.models import EntityRef
from kubo.errors import MalformedOutputError, RateLimitExhausted
from kubo.workers.distiller import DistillOutput
from scripts import distill_pilot as dp

T = TypeVar("T", bound=BaseModel)


class _FakeExecutor:
    """Fake de `Executor`: devolve `outputs[i]` ou levanta `errors[i]` na i-ésima chamada."""

    def __init__(self, outputs=None, errors=None) -> None:
        self._outputs = outputs or {}
        self._errors = errors or {}
        self.calls = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        idx = self.calls
        self.calls += 1
        if idx in self._errors:
            raise self._errors[idx]
        return cast(T, self._outputs[idx])


def test_run_candidate_collects_summaries_and_pauses_between_items() -> None:
    """run_candidate devolve o summary de cada item e faz pacing entre eles (nunca antes do 1º)."""
    items = [("item:1", "conteúdo um"), ("item:2", "conteúdo dois")]
    executor = _FakeExecutor(
        outputs={
            0: DistillOutput(summary="resumo 1", entities=[]),
            1: DistillOutput(summary="resumo 2", entities=[]),
        }
    )
    sleeps: list[float] = []
    result = dp.run_candidate("cand/x", items, executor=executor, delay=2.0, sleep=sleeps.append)

    assert result.summaries == {"item:1": "resumo 1", "item:2": "resumo 2"}
    assert sleeps == [2.0]  # uma pausa, entre o 1º e o 2º


def test_run_candidate_counts_failures_and_leaves_summary_none() -> None:
    """Malformado, rate-limit e provider-error são contados; o summary do item vira None
    e o piloto nunca explode."""
    items = [("a", "ca"), ("b", "cb"), ("c", "cc")]
    executor = _FakeExecutor(
        errors={
            0: MalformedOutputError("malformado"),
            1: RateLimitExhausted("limite", scope="minute"),
        },
        outputs={2: DistillOutput(summary="ok", entities=[])},
    )
    result = dp.run_candidate("cand/x", items, executor=executor, sleep=lambda _: None)

    assert result.malformed == 1
    assert result.rate_limited == 1
    assert result.summaries == {"a": None, "b": None, "c": "ok"}


def test_run_candidate_aborts_early_on_daily_exhaustion() -> None:
    """RateLimitExhausted(scope='day') aborta o candidato: os itens restantes NÃO são
    tentados (não repetem o backoff só para falhar; no pago, não gastam dinheiro à toa)."""
    items = [("a", "ca"), ("b", "cb"), ("c", "cc")]
    executor = _FakeExecutor(errors={0: RateLimitExhausted("quota diária", scope="day")})
    result = dp.run_candidate("cand/x", items, executor=executor, sleep=lambda _: None)

    assert executor.calls == 1  # parou no 1º; b e c nunca foram chamados
    assert result.rate_limited == 1
    assert "b" not in result.summaries and "c" not in result.summaries


def test_run_candidate_continues_on_minute_exhaustion() -> None:
    """scope='minute' NÃO aborta: o item seguinte ainda é tentado (janela de minuto)."""
    items = [("a", "ca"), ("b", "cb")]
    executor = _FakeExecutor(
        errors={0: RateLimitExhausted("janela de minuto", scope="minute")},
        outputs={1: DistillOutput(summary="ok b", entities=[])},
    )
    result = dp.run_candidate("cand/x", items, executor=executor, sleep=lambda _: None)

    assert executor.calls == 2  # seguiu para o 2º
    assert result.summaries == {"a": None, "b": "ok b"}


def test_run_candidate_counts_kept_entities_verbatim_filtered() -> None:
    """entity_counts guarda só o COUNT de entidades pós-`filter_present_entities`
    (verbatim no content) — nunca os nomes; a entidade não-presente é descartada."""
    items = [("a", "texto sobre a Anthropic")]
    executor = _FakeExecutor(
        outputs={
            0: DistillOutput(
                summary="resumo",
                entities=[EntityRef(name="Anthropic"), EntityRef(name="Inexistente")],
            )
        }
    )
    result = dp.run_candidate("cand/x", items, executor=executor, sleep=lambda _: None)

    assert result.entity_counts["a"] == 1  # só a entidade presente no content


def test_render_pilot_shows_content_baseline_and_each_candidate() -> None:
    """O doc traz content, baseline (llama) e o summary de cada candidato, + contadores."""
    items = [("item:1", "conteúdo original")]
    baselines = {"item:1": "summary do llama"}
    r1 = dp.PilotResult(model="cand/a", summaries={"item:1": "summary A"})
    r2 = dp.PilotResult(model="cand/b", summaries={"item:1": None}, malformed=1)

    doc = dp.render_pilot(items, baselines, [r1, r2])

    assert "conteúdo original" in doc
    assert "summary do llama" in doc
    assert "summary A" in doc
    assert "cand/a" in doc
    assert "malformed=1" in doc  # contador do candidato que falhou


def test_render_pilot_truncates_content_at_cap() -> None:
    """Content maior que o cap é truncado no doc (o candidato viu só o cap)."""
    items = [("item:1", "y" * 50)]
    doc = dp.render_pilot(items, {}, [], input_char_cap=10)
    assert "y" * 10 in doc
    assert "y" * 11 not in doc
