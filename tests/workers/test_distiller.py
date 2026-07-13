"""Worker `distiller` — RED do Marco 8.6 Peça 5 (ADR-0013 §III).

Unit puro: sem SurrealDB, sem rede, sem LiteLLM real. `ctx` é um `_FakeCtx`
(dataclass simples) que satisfaz `RunContext` estruturalmente; o executor de
LLM é `_FakeExecutor` (fila de respostas/erros por chamada) — nenhum teste
aqui toca `ApiExecutor`/LiteLLM (CLAUDE.md: "LLMs em testes sempre mockados").

Comportamento fixado (não implementação):
- ref é ECOADO do `ItemView` de origem, nunca inventado (§III.3).
- item malformado é pulado e contado; o run segue (§III.6).
- rate limit esgotado PARA o loop e devolve o parcial + `error` (§V, ADR-0009 §VII).
- content é truncado ao `input_char_cap` antes de ir ao executor (advisor h3).
- embedder ausente é erro de configuração, não silencioso.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, cast

import pytest
import structlog
from pydantic import BaseModel

from kubo.contracts.models import DistilledPayload, EntityRef, Payload
from kubo.contracts.worker import ItemView
from kubo.embedding import Embedder
from kubo.errors import ConfigError, EmbeddingError, MalformedOutputError, RateLimitExhausted
from kubo.workers.distiller import (
    DistillerConfig,
    DistillerWorker,
    DistillOutput,
    filter_present_entities,
)

T = TypeVar("T", bound=BaseModel)


class _FakeExecutor:
    """Fake de `Executor`: devolve `outputs[i]` ou levanta `errors[i]` na i-ésima
    chamada (0-based, na ordem em que `run` invoca `complete`). Registra o
    `untrusted_content` recebido em cada chamada — usado no teste de truncamento."""

    def __init__(
        self,
        outputs: dict[int, DistillOutput] | None = None,
        errors: dict[int, Exception] | None = None,
    ) -> None:
        self._outputs = outputs or {}
        self._errors = errors or {}
        self.received_content: list[str] = []
        self.call_count = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        idx = self.call_count
        self.call_count += 1
        self.received_content.append(untrusted_content)
        if idx in self._errors:
            raise self._errors[idx]
        return cast(T, self._outputs[idx])


class _FakeEmbedder:
    """Fake de `Embedder`: tripla fixa + vetor fixo de 768 floats por texto."""

    model = "gemini-embedding-001"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]


class _FakeKnowledge:
    """Fake de `KnowledgeReader`: devolve os `ItemView` canned, ignora `limit`."""

    def __init__(self, items: list[ItemView]) -> None:
        self._items = items

    def items_to_distill(self, limit: int) -> list[ItemView]:
        return list(self._items)

    def distilled_for_digest(self, destination: str, limit: int) -> list[Any]:
        """Não usado pelo distiller; presente só para satisfazer o Protocol KnowledgeReader."""
        return []

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[Any]:
        """Não usado pelo distiller; presente só para satisfazer o Protocol KnowledgeReader."""
        return []


@dataclass
class _FakeCtx:
    """Fake de `RunContext`: atributos simples satisfazem o Protocol estruturalmente
    (mesmo padrão do `RunContext` concreto em kubo/runtime/context.py, sem depender
    dele — este teste não toca runtime/store)."""

    config: DistillerConfig
    integrations: dict[str, object]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: Embedder | None


def _ctx(config: DistillerConfig, knowledge: _FakeKnowledge, embedder: Embedder | None) -> _FakeCtx:
    """Monta um `RunContext` fake pronto pra passar a `DistillerWorker.run`."""
    return _FakeCtx(
        config=config,
        integrations={},
        knowledge=knowledge,
        logger=structlog.get_logger(),
        embedder=embedder,
    )


def _as_distilled(payload: Payload) -> DistilledPayload:
    """Estreita um `Payload` da união discriminada para `DistilledPayload`."""
    assert isinstance(payload, DistilledPayload)
    return payload


def test_run_distills_items_into_payloads_with_ref_summary_entities_and_chunks() -> None:
    """Caminho feliz: 2 itens → 2 DistilledPayload, cada um com ref/summary/entities
    do LLM e chunks já embeddados com a tripla do embedder."""
    items = [
        ItemView(ref=0, title="t0", content="conteudo zero sobre a Anthropic"),
        ItemView(ref=1, title="t1", content="conteudo um sobre a Anthropic"),
    ]
    outputs = {
        0: DistillOutput(summary="resumo 0", entities=[EntityRef(name="Anthropic", kind="org")]),
        1: DistillOutput(summary="resumo 1", entities=[EntityRef(name="Anthropic", kind="org")]),
    }
    executor = _FakeExecutor(outputs=outputs)
    embedder = _FakeEmbedder()
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), embedder)

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    assert len(result.payloads) == 2
    payload0 = _as_distilled(result.payloads[0])
    payload1 = _as_distilled(result.payloads[1])
    assert payload0.ref == 0
    assert payload1.ref == 1
    assert payload0.summary == "resumo 0"
    assert payload0.entities == [EntityRef(name="Anthropic", kind="org")]
    assert len(payload0.chunks) >= 1
    chunk = payload0.chunks[0]
    assert chunk.model == embedder.model
    assert chunk.dim == embedder.dim
    assert chunk.task_type == embedder.task_type
    assert chunk.embedding == [0.1] * 768
    assert result.stats.model_dump()["distilled"] == 2


def test_run_skips_malformed_item_and_counts_it_without_failing_the_run() -> None:
    """1º item malformado é pulado e contado; 2º item segue destilado normalmente."""
    items = [
        ItemView(ref=0, title=None, content="c0"),
        ItemView(ref=1, title=None, content="c1"),
    ]
    executor = _FakeExecutor(
        errors={0: MalformedOutputError("saída não valida contra o schema")},
        outputs={1: DistillOutput(summary="resumo 1", entities=[])},
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    assert len(result.payloads) == 1
    assert _as_distilled(result.payloads[0]).ref == 1
    stats = result.stats.model_dump()
    assert stats["malformed"] == 1
    assert stats["distilled"] == 1


def test_run_stops_on_rate_limit_exhausted_and_returns_partial_with_error() -> None:
    """Rate limit esgotado no 2º de 3 itens PARA o loop: devolve só o 1º payload,
    `error` estruturado, e NÃO chama o executor para o 3º item."""
    items = [
        ItemView(ref=0, title=None, content="c0"),
        ItemView(ref=1, title=None, content="c1"),
        ItemView(ref=2, title=None, content="c2"),
    ]
    executor = _FakeExecutor(
        outputs={0: DistillOutput(summary="resumo 0", entities=[])},
        errors={1: RateLimitExhausted("quota esgotada após 3 tentativas")},
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert len(result.payloads) == 1
    assert _as_distilled(result.payloads[0]).ref == 0
    assert result.error is not None
    assert result.error.kind == "rate_limit_exhausted"
    assert executor.call_count == 2  # nunca chamou pro 3º item


def test_run_maps_rate_limit_scope_minute_to_error_kind() -> None:
    """RateLimitExhausted(scope='minute') vira `error.kind == 'rate_limit_minute'`
    — visível em Execuções para o dono discriminar janela de minuto de janela de dia (A2)."""
    items = [ItemView(ref=0, title=None, content="c0")]
    executor = _FakeExecutor(errors={0: RateLimitExhausted("janela de minuto", scope="minute")})
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is not None
    assert result.error.kind == "rate_limit_minute"


def test_run_maps_rate_limit_scope_day_to_error_kind() -> None:
    """RateLimitExhausted(scope='day') vira `error.kind == 'rate_limit_day'` (A2)."""
    items = [ItemView(ref=0, title=None, content="c0")]
    executor = _FakeExecutor(errors={0: RateLimitExhausted("janela de dia", scope="day")})
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is not None
    assert result.error.kind == "rate_limit_day"


class _FailingEmbedder:
    """Fake de `Embedder` que embedda `fail_at` vezes com sucesso e depois levanta
    `EmbeddingError` — simula a API de embedding caindo no meio de um lote (A3)."""

    model = "gemini-embedding-001"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def __init__(self, fail_at: int) -> None:
        self._fail_at = fail_at
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.calls >= self._fail_at:
            raise EmbeddingError("API de embedding falhou")
        self.calls += 1
        return [[0.1] * 768 for _ in texts]


def test_run_stops_on_embedding_error_and_returns_partial_with_error() -> None:
    """EmbeddingError no meio do lote é falha SISTÊMICA (análoga a RateLimitExhausted, E2):
    para o loop, PERSISTE o parcial já destilado e devolve `error` estruturado — no dreno
    pago, perder o parcial seria dinheiro re-gasto a cada re-run."""
    items = [
        ItemView(ref=0, title=None, content="c0"),
        ItemView(ref=1, title=None, content="c1"),
        ItemView(ref=2, title=None, content="c2"),
    ]
    executor = _FakeExecutor(
        outputs={
            0: DistillOutput(summary="resumo 0", entities=[]),
            1: DistillOutput(summary="resumo 1", entities=[]),
            2: DistillOutput(summary="resumo 2", entities=[]),
        }
    )
    embedder = _FailingEmbedder(fail_at=1)  # item 0 embedda; item 1 falha
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), embedder)

    result = DistillerWorker(executor).run(ctx)

    assert len(result.payloads) == 1  # só o 1º item, já destilado, sobrevive
    assert _as_distilled(result.payloads[0]).ref == 0
    assert result.error is not None
    assert result.error.kind == "embedding_failed"
    assert executor.call_count == 2  # parou no item 1, nunca chamou o 3º


def test_run_truncates_content_to_input_char_cap_before_calling_executor() -> None:
    """Content de 30000 chars com input_char_cap=20000 chega ao executor capado,
    e o truncamento é contado em stats."""
    long_content = "a" * 30000
    items = [ItemView(ref=0, title=None, content=long_content)]
    executor = _FakeExecutor(outputs={0: DistillOutput(summary="resumo", entities=[])})
    config = DistillerConfig(input_char_cap=20000)
    ctx = _ctx(config, _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert len(executor.received_content[0]) <= 20000
    assert result.stats.model_dump()["truncated"] >= 1


def test_run_raises_config_error_when_embedder_missing() -> None:
    """Sem embedder no ctx, `run` levanta ConfigError — o runner traduz pra erro
    estruturado; o worker não segue destilando sem como gerar chunks."""
    items = [ItemView(ref=0, title=None, content="c0")]
    executor = _FakeExecutor(outputs={0: DistillOutput(summary="resumo", entities=[])})
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), embedder=None)

    with pytest.raises(ConfigError):
        DistillerWorker(executor).run(ctx)


def test_run_filters_entities_not_present_in_content() -> None:
    """Filtro verbatim (ADR-0013 §V emenda): entidade cujo `name` (casefold) não é
    substring do content enviado ao LLM é descartada e contada em
    `entities_filtered` — defesa estrutural contra injeção de entidade via
    conteúdo coletado, independente do LLM obedecer a instrução."""
    items = [
        ItemView(ref=0, title=None, content="Texto sobre a Anthropic e seus modelos."),
    ]
    executor = _FakeExecutor(
        outputs={
            0: DistillOutput(
                summary="resumo",
                entities=[
                    EntityRef(name="Anthropic", kind="org"),
                    EntityRef(name="INJETADA_FANTASMA", kind="malware"),
                ],
            )
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    payload0 = _as_distilled(result.payloads[0])
    assert payload0.entities == [EntityRef(name="Anthropic", kind="org")]
    assert result.stats.model_dump()["entities_filtered"] == 1


def test_run_filters_entities_case_insensitively() -> None:
    """O casefold do filtro verbatim é case-insensitive: entidade em caixa
    diferente da que aparece no content ainda é considerada presente."""
    items = [
        ItemView(ref=0, title=None, content="Texto sobre a Anthropic e seus modelos."),
    ]
    executor = _FakeExecutor(
        outputs={
            0: DistillOutput(summary="resumo", entities=[EntityRef(name="anthropic", kind="org")])
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    payload0 = _as_distilled(result.payloads[0])
    assert payload0.entities == [EntityRef(name="anthropic", kind="org")]
    assert result.stats.model_dump()["entities_filtered"] == 0


def test_filter_present_entities_keeps_present_drops_absent_case_insensitively() -> None:
    """`filter_present_entities` direto (sem passar pelo worker): entidade presente
    (mesmo em caixa diferente) é mantida; entidade ausente do content é descartada."""
    content = "Texto sobre a Anthropic e seus modelos."
    entities = [
        EntityRef(name="anthropic", kind="org"),
        EntityRef(name="INJETADA_FANTASMA", kind="malware"),
    ]

    kept = filter_present_entities(entities, content)

    assert kept == [EntityRef(name="anthropic", kind="org")]


def test_run_skips_item_with_whitespace_only_summary_and_counts_empty_summary() -> None:
    """summary só-whitespace (passa min_length=1, mas chunk_text devolve []) não vira
    DistilledPayload não-buscável: é pulado, contado em `empty_summary`, e o run segue
    destilando os outros itens (Minor, qualidade — achado de code review)."""
    items = [
        ItemView(ref=0, title=None, content="c0"),
        ItemView(ref=1, title=None, content="c1"),
    ]
    executor = _FakeExecutor(
        outputs={
            0: DistillOutput(summary="   ", entities=[]),
            1: DistillOutput(summary="resumo 1", entities=[]),
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    assert len(result.payloads) == 1
    assert _as_distilled(result.payloads[0]).ref == 1
    stats = result.stats.model_dump()
    assert stats["empty_summary"] >= 1
    assert stats["distilled"] == 1


def test_run_echoes_item_ref_never_invents_it() -> None:
    """ref do payload é o MESMO do ItemView de origem (42, não 0/índice de loop) —
    o pareamento é programático, o LLM nunca escolhe/ecoa ref (§III.3)."""
    items = [ItemView(ref=42, title=None, content="c0")]
    executor = _FakeExecutor(outputs={0: DistillOutput(summary="resumo", entities=[])})
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert _as_distilled(result.payloads[0]).ref == 42
