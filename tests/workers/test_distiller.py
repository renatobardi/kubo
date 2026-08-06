"""Worker `distiller` — funil invertido (ADR-0013 §III, ADR-0051 §I: KUBO-193).

Unit puro: sem SurrealDB, sem rede, sem LiteLLM real. `ctx` é um `_FakeCtx`
(dataclass simples) que satisfaz `RunContext` estruturalmente; o executor de
LLM é `_FakeExecutor` (fila de respostas/erros por chamada) — nenhum teste
aqui toca `ApiExecutor`/LiteLLM (CLAUDE.md: "LLMs em testes sempre mockados").

Comportamento fixado (não implementação):
- Cada item pendente passa por UMA chamada de pontuação (título+URL, ADR-0051
  §I.1) — se aprovado (score >= min_score), uma SEGUNDA chamada de destilação
  (conteúdo). Reprovado nunca chama a 2ª.
- ref é ECOADO do `ItemView` de origem, nunca inventado (§III.3).
- item malformado (em QUALQUER das duas chamadas) é pulado e contado; o run segue.
- rate limit esgotado PARA o loop e devolve o parcial + `error` (§V, ADR-0009 §VII).
- content é truncado ao `input_char_cap` antes de ir ao executor de destilação.
- embedder ausente é erro de configuração, não silencioso.
- markdown estrutural (`## ...`) é removido do summary ANTES de persistir.
- título gerado só quando o item não tem título; nunca sobrescreve item.title.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, cast

import pytest
import structlog
from pydantic import BaseModel

from kubo.contracts.models import DistilledPayload, EntityRef, Payload, ScorePayload
from kubo.contracts.worker import ItemView
from kubo.embedding import Embedder
from kubo.errors import ConfigError, EmbeddingError, MalformedOutputError, RateLimitExhausted
from kubo.workers.distiller import (
    DistillerConfig,
    DistillerWorker,
    DistillOutput,
    ScoreOutput,
    filter_present_entities,
)

T = TypeVar("T", bound=BaseModel)

_WORK_CONTEXT = "Curte IA aplicada e infraestrutura."


class _FakeExecutor:
    """Fake de `Executor`: devolve `outputs[i]` ou levanta `errors[i]` na i-ésima
    chamada (0-based, na ordem em que `run` invoca `complete` — pontuação e
    destilação COMPARTILHAM o mesmo contador). Registra o `untrusted_content`
    recebido em cada chamada — usado no teste de truncamento/conteúdo de nota."""

    def __init__(
        self,
        outputs: dict[int, BaseModel] | None = None,
        errors: dict[int, Exception] | None = None,
    ) -> None:
        self._outputs = outputs or {}
        self._errors = errors or {}
        self.received_content: list[str] = []
        self.received_instructions: list[str] = []
        self.received_models: list[type[BaseModel]] = []
        self.call_count = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[T]) -> T:
        idx = self.call_count
        self.call_count += 1
        self.received_content.append(untrusted_content)
        self.received_instructions.append(instruction)
        self.received_models.append(response_model)
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

    def __init__(self, items: list[ItemView], work_context: str = _WORK_CONTEXT) -> None:
        self._items = items
        self._work_context = work_context

    def items_to_score(self, limit: int) -> list[ItemView]:
        return list(self._items)

    def work_context(self) -> str:
        return self._work_context

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


def _as_score(payload: Payload) -> ScorePayload:
    """Estreita um `Payload` da união discriminada para `ScorePayload`."""
    assert isinstance(payload, ScorePayload)
    return payload


def _approve(score: int = 8) -> ScoreOutput:
    """`ScoreOutput` aprovado (>= min_score default) sem título gerado."""
    return ScoreOutput(score=score)


def test_run_scores_then_distills_approved_items_with_ref_summary_entities_and_chunks() -> None:
    """Caminho feliz: 2 itens aprovados na pontuação → cada um vira 1 ScorePayload
    + 1 DistilledPayload, na ordem certa (pontua item 0, destila item 0, pontua
    item 1, destila item 1)."""
    items = [
        ItemView(ref=0, title="t0", url="https://x/0", content="conteudo zero sobre a Anthropic"),
        ItemView(ref=1, title="t1", url="https://x/1", content="conteudo um sobre a Anthropic"),
    ]
    outputs: dict[int, BaseModel] = {
        0: _approve(9),
        1: DistillOutput(summary="resumo 0", entities=[EntityRef(name="Anthropic", kind="org")]),
        2: _approve(7),
        3: DistillOutput(summary="resumo 1", entities=[EntityRef(name="Anthropic", kind="org")]),
    }
    executor = _FakeExecutor(outputs=outputs)
    embedder = _FakeEmbedder()
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), embedder)

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    scores = [_as_score(p) for p in result.payloads if isinstance(p, ScorePayload)]
    distilled = [_as_distilled(p) for p in result.payloads if isinstance(p, DistilledPayload)]
    assert {s.ref for s in scores} == {0, 1}
    assert {d.ref for d in distilled} == {0, 1}
    d0 = next(d for d in distilled if d.ref == 0)
    assert d0.summary == "resumo 0"
    assert d0.entities == [EntityRef(name="Anthropic", kind="org")]
    assert len(d0.chunks) >= 1
    chunk = d0.chunks[0]
    assert chunk.model == embedder.model
    assert chunk.dim == embedder.dim
    assert chunk.task_type == embedder.task_type
    assert chunk.embedding == [0.1] * 768
    stats = result.stats.model_dump()
    assert stats["scored"] == 2
    assert stats["approved"] == 2
    assert stats["distilled"] == 2


def test_score_call_receives_title_and_url_not_content() -> None:
    """A pontuação roda sobre título+URL (ADR-0051 §I.1) — o content bruto do
    item NUNCA entra na 1ª chamada."""
    items = [ItemView(ref=0, title="Meu Título", url="https://x/y", content="CONTEUDO_SECRETO")]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo", entities=[])}
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    DistillerWorker(executor).run(ctx)

    score_call_content = executor.received_content[0]
    assert "Meu Título" in score_call_content
    assert "https://x/y" in score_call_content
    assert "CONTEUDO_SECRETO" not in score_call_content
    # defesa contra troca de schema entre as 2 chamadas (achado CodeRabbit no PR #223)
    assert executor.received_models == [ScoreOutput, DistillOutput]


def test_score_instruction_carries_tenant_work_context() -> None:
    """A instrução de pontuação embute o work_context do tenant (ADR-0051 §I.1) —
    é a alavanca de curadoria, sem ela a nota não tem contra o que comparar."""
    items = [ItemView(ref=0, title="t", url=None, content="c")]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo", entities=[])}
    )
    ctx = _ctx(
        DistillerConfig(), _FakeKnowledge(items, work_context="Adora Rust."), _FakeEmbedder()
    )

    DistillerWorker(executor).run(ctx)

    assert "Adora Rust." in executor.received_instructions[0]


def test_score_instruction_falls_back_when_work_context_is_empty() -> None:
    """Tenant sem work_context (`get_tenant_work_context` devolve ""): a pontuação
    roda com o marcador de contexto ausente, nunca com string vazia colada na
    instrução (achado CodeRabbit no PR #223 — cenário real: dono sem perfil)."""
    items = [ItemView(ref=0, title="t", url=None, content="c")]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo", entities=[])}
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items, work_context=""), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    assert "(sem contexto definido)" in executor.received_instructions[0]


def test_item_below_cutoff_gets_scored_but_never_distilled() -> None:
    """Item reprovado (score < min_score) vira ScorePayload mas NUNCA chama o
    executor de destilação — reprovação é definitiva (ADR-0051 §I.3/4)."""
    items = [ItemView(ref=0, title="t", url=None, content="c")]
    config = DistillerConfig(min_score=6)
    executor = _FakeExecutor(outputs={0: ScoreOutput(score=3)})
    ctx = _ctx(config, _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    assert executor.call_count == 1  # só a pontuação, nunca a destilação
    assert len(result.payloads) == 1
    score = _as_score(result.payloads[0])
    assert score.ref == 0
    assert score.score == 3
    stats = result.stats.model_dump()
    assert stats["scored"] == 1
    assert stats["approved"] == 0
    assert stats["distilled"] == 0


def test_item_at_exact_cutoff_is_approved() -> None:
    """score == min_score passa o corte (inclusive, não estrito)."""
    items = [ItemView(ref=0, title="t", url=None, content="c")]
    config = DistillerConfig(min_score=6)
    executor = _FakeExecutor(
        outputs={0: ScoreOutput(score=6), 1: DistillOutput(summary="resumo", entities=[])}
    )
    ctx = _ctx(config, _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert executor.call_count == 2
    stats = result.stats.model_dump()
    assert stats["approved"] == 1
    assert stats["distilled"] == 1


def test_generated_title_only_when_item_has_no_title() -> None:
    """Item COM título: generated_title do LLM (se vier) é descartado — nunca
    sobrescreve nem duplica (ADR-0051 §IV.1, defesa na gravação)."""
    items = [ItemView(ref=0, title="Título da Fonte", url=None, content="c")]
    executor = _FakeExecutor(
        outputs={
            0: ScoreOutput(score=9, generated_title="Título Inventado"),
            1: DistillOutput(summary="resumo", entities=[]),
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    score = _as_score(next(p for p in result.payloads if isinstance(p, ScorePayload)))
    assert score.generated_title is None


def test_generated_title_populated_when_item_has_no_title() -> None:
    """Item SEM título: generated_title do LLM vai pro ScorePayload — campo
    próprio, nunca em item.title (ADR-0051 §IV.1)."""
    items = [ItemView(ref=0, title=None, url="https://x/y", content="c")]
    executor = _FakeExecutor(
        outputs={
            0: ScoreOutput(score=9, generated_title="Título Gerado"),
            1: DistillOutput(summary="resumo", entities=[]),
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    score = _as_score(next(p for p in result.payloads if isinstance(p, ScorePayload)))
    assert score.generated_title == "Título Gerado"


def test_structural_markdown_is_stripped_from_summary_before_persisting() -> None:
    """Cabeçalho `## SUMMARY` vazado na saída do LLM não chega ao campo persistido
    (ADR-0051 §III) — defesa NA GRAVAÇÃO, não só no prompt."""
    items = [ItemView(ref=0, title="t", url=None, content="c")]
    executor = _FakeExecutor(
        outputs={
            0: _approve(9),
            1: DistillOutput(
                summary="## SUMMARY\nTexto de verdade sobre o assunto.\n## CONCEPTS\nfoo",
                entities=[],
            ),
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    distilled = _as_distilled(next(p for p in result.payloads if isinstance(p, DistilledPayload)))
    assert "## SUMMARY" not in distilled.summary
    assert "## CONCEPTS" not in distilled.summary
    assert "Texto de verdade sobre o assunto." in distilled.summary


def test_run_skips_malformed_item_and_counts_it_without_failing_the_run() -> None:
    """1º item malformado NA PONTUAÇÃO é pulado e contado; 2º item segue
    pontuado+destilado normalmente."""
    items = [
        ItemView(ref=0, title=None, url=None, content="c0"),
        ItemView(ref=1, title=None, url=None, content="c1"),
    ]
    executor = _FakeExecutor(
        errors={0: MalformedOutputError("saída não valida contra o schema")},
        outputs={1: _approve(9), 2: DistillOutput(summary="resumo 1", entities=[])},
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    distilled = [p for p in result.payloads if isinstance(p, DistilledPayload)]
    assert len(distilled) == 1
    assert _as_distilled(distilled[0]).ref == 1
    stats = result.stats.model_dump()
    assert stats["malformed"] == 1
    assert stats["distilled"] == 1


def test_run_skips_malformed_distill_call_and_does_not_persist_the_score() -> None:
    """Malformado NA DESTILAÇÃO (não na pontuação, achado CodeRabbit no PR #223):
    a nota NÃO é persistida — só vira ScorePayload se a destilação também
    suceder. Persistir a nota aqui estrangularia o item pra sempre: `scored_for`
    gravado o excluiria de `items_to_score` em todo run futuro, sem fila de
    retry pra aprovado-mas-nunca-destilado. Sem nota, o item é repontuado do
    zero no próximo run — retry natural."""
    items = [ItemView(ref=0, title=None, url=None, content="c0")]
    executor = _FakeExecutor(
        outputs={0: _approve(9)},
        errors={1: MalformedOutputError("saída não valida")},
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    assert result.payloads == []
    stats = result.stats.model_dump()
    assert stats["malformed"] == 1
    assert stats["approved"] == 1  # visibilidade do funil, independente da persistência
    assert stats["distilled"] == 0


def test_run_stops_on_rate_limit_exhausted_during_scoring() -> None:
    """Rate limit esgotado NA PONTUAÇÃO do 2º de 3 itens PARA o loop: devolve só
    o payload do 1º item, `error` estruturado, nunca chega ao 3º item."""
    items = [
        ItemView(ref=0, title=None, url=None, content="c0"),
        ItemView(ref=1, title=None, url=None, content="c1"),
        ItemView(ref=2, title=None, url=None, content="c2"),
    ]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo 0", entities=[])},
        errors={2: RateLimitExhausted("quota esgotada após 3 tentativas")},
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert len(result.payloads) == 2  # score + distilled do item 0
    assert result.error is not None
    assert result.error.kind == "rate_limit_exhausted"
    assert executor.call_count == 3  # nunca chegou no item 2


def test_run_stops_on_rate_limit_exhausted_during_distillation() -> None:
    """Rate limit esgotado NA DESTILAÇÃO também para o loop e devolve o parcial —
    a nota do item que estourou NÃO é persistida (mesma regra do malformado):
    sem destilado confirmado, sem scored_for, retry do zero no próximo run."""
    items = [
        ItemView(ref=0, title=None, url=None, content="c0"),
        ItemView(ref=1, title=None, url=None, content="c1"),
    ]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 2: _approve(9)},
        errors={1: RateLimitExhausted("quota esgotada")},
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is not None
    assert result.error.kind == "rate_limit_exhausted"
    assert result.payloads == []
    assert executor.call_count == 2  # nunca chegou a pontuar o item 1


def test_run_maps_rate_limit_scope_minute_to_error_kind() -> None:
    """RateLimitExhausted(scope='minute') vira `error.kind == 'rate_limit_minute'`
    — visível em Execuções para o dono discriminar janela de minuto de janela de dia (A2)."""
    items = [ItemView(ref=0, title=None, url=None, content="c0")]
    executor = _FakeExecutor(errors={0: RateLimitExhausted("janela de minuto", scope="minute")})
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is not None
    assert result.error.kind == "rate_limit_minute"


def test_run_maps_rate_limit_scope_day_to_error_kind() -> None:
    """RateLimitExhausted(scope='day') vira `error.kind == 'rate_limit_day'` (A2)."""
    items = [ItemView(ref=0, title=None, url=None, content="c0")]
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
        ItemView(ref=0, title=None, url=None, content="c0"),
        ItemView(ref=1, title=None, url=None, content="c1"),
    ]
    executor = _FakeExecutor(
        outputs={
            0: _approve(9),
            1: DistillOutput(summary="resumo 0", entities=[]),
            2: _approve(9),
            3: DistillOutput(summary="resumo 1", entities=[]),
        }
    )
    embedder = _FailingEmbedder(fail_at=1)  # item 0 embedda; item 1 falha
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), embedder)

    result = DistillerWorker(executor).run(ctx)

    distilled = [p for p in result.payloads if isinstance(p, DistilledPayload)]
    scores = [p for p in result.payloads if isinstance(p, ScorePayload)]
    assert len(distilled) == 1  # só o 1º item, já destilado, sobrevive
    assert _as_distilled(distilled[0]).ref == 0
    # item 1 pontuou mas o embedding falhou — nota NÃO persiste (mesma regra do
    # malformado/rate-limit): sem destilado confirmado, sem scored_for.
    assert {s.ref for s in scores} == {0}
    assert result.error is not None
    assert result.error.kind == "embedding_failed"


def test_run_truncates_content_to_input_char_cap_before_calling_executor() -> None:
    """Content de 30000 chars com input_char_cap=20000 chega ao executor de
    DESTILAÇÃO capado (a pontuação nunca vê content, só título/url); o
    truncamento é contado em stats."""
    long_content = "a" * 30000
    items = [ItemView(ref=0, title=None, url=None, content=long_content)]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo", entities=[])}
    )
    config = DistillerConfig(input_char_cap=20000)
    ctx = _ctx(config, _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert len(executor.received_content[1]) <= 20000
    assert result.stats.model_dump()["truncated"] >= 1


def test_run_raises_config_error_when_embedder_missing() -> None:
    """Sem embedder no ctx, `run` levanta ConfigError — o runner traduz pra erro
    estruturado; o worker não segue destilando sem como gerar chunks."""
    items = [ItemView(ref=0, title=None, url=None, content="c0")]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo", entities=[])}
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), embedder=None)

    with pytest.raises(ConfigError):
        DistillerWorker(executor).run(ctx)


def test_run_filters_entities_not_present_in_content() -> None:
    """Filtro verbatim (ADR-0013 §V emenda): entidade cujo `name` (casefold) não é
    substring do content enviado ao LLM é descartada e contada em
    `entities_filtered` — defesa estrutural contra injeção de entidade via
    conteúdo coletado, independente do LLM obedecer a instrução."""
    items = [
        ItemView(ref=0, title=None, url=None, content="Texto sobre a Anthropic e seus modelos."),
    ]
    executor = _FakeExecutor(
        outputs={
            0: _approve(9),
            1: DistillOutput(
                summary="resumo",
                entities=[
                    EntityRef(name="Anthropic", kind="org"),
                    EntityRef(name="INJETADA_FANTASMA", kind="malware"),
                ],
            ),
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    distilled = _as_distilled(next(p for p in result.payloads if isinstance(p, DistilledPayload)))
    assert distilled.entities == [EntityRef(name="Anthropic", kind="org")]
    assert result.stats.model_dump()["entities_filtered"] == 1


def test_run_filters_entities_case_insensitively() -> None:
    """O casefold do filtro verbatim é case-insensitive: entidade em caixa
    diferente da que aparece no content ainda é considerada presente."""
    items = [
        ItemView(ref=0, title=None, url=None, content="Texto sobre a Anthropic e seus modelos."),
    ]
    executor = _FakeExecutor(
        outputs={
            0: _approve(9),
            1: DistillOutput(summary="resumo", entities=[EntityRef(name="anthropic", kind="org")]),
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    distilled = _as_distilled(next(p for p in result.payloads if isinstance(p, DistilledPayload)))
    assert distilled.entities == [EntityRef(name="anthropic", kind="org")]
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
        ItemView(ref=0, title=None, url=None, content="c0"),
        ItemView(ref=1, title=None, url=None, content="c1"),
    ]
    executor = _FakeExecutor(
        outputs={
            0: _approve(9),
            1: DistillOutput(summary="   ", entities=[]),
            2: _approve(9),
            3: DistillOutput(summary="resumo 1", entities=[]),
        }
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    distilled = [p for p in result.payloads if isinstance(p, DistilledPayload)]
    scores = [p for p in result.payloads if isinstance(p, ScorePayload)]
    assert len(distilled) == 1
    assert _as_distilled(distilled[0]).ref == 1
    # item 0 (summary vazio pós-limpeza) não vira nota — mesma regra do malformado:
    # sem destilado confirmado, sem scored_for, retry do zero no próximo run.
    assert {s.ref for s in scores} == {1}
    stats = result.stats.model_dump()
    assert stats["empty_summary"] >= 1
    assert stats["distilled"] == 1


def test_run_echoes_item_ref_never_invents_it() -> None:
    """ref do payload é o MESMO do ItemView de origem (42, não 0/índice de loop) —
    o pareamento é programático, o LLM nunca escolhe/ecoa ref (§III.3)."""
    items = [ItemView(ref=42, title=None, url=None, content="c0")]
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo", entities=[])}
    )
    ctx = _ctx(DistillerConfig(), _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    distilled = _as_distilled(next(p for p in result.payloads if isinstance(p, DistilledPayload)))
    assert distilled.ref == 42
    score = _as_score(next(p for p in result.payloads if isinstance(p, ScorePayload)))
    assert score.ref == 42


def test_max_distill_items_breaks_the_loop_without_stranding_unscored_items() -> None:
    """Orçamento de destilação exaurido PARA o loop inteiro (break, não continue) —
    itens ainda não pontuados ficam SEM nota, elegíveis de novo no próximo run.
    Aprovar um item que não teria como destilar o estranharia pra sempre (nota já
    gravada exclui de items_to_score, sem fila de retry pra aprovado-mas-não-destilado)."""
    items = [
        ItemView(ref=0, title=None, url=None, content="c0"),
        ItemView(ref=1, title=None, url=None, content="c1"),
        ItemView(ref=2, title=None, url=None, content="c2"),
    ]
    config = DistillerConfig(max_distill_items=1)
    executor = _FakeExecutor(
        outputs={0: _approve(9), 1: DistillOutput(summary="resumo 0", entities=[])}
    )
    ctx = _ctx(config, _FakeKnowledge(items), _FakeEmbedder())

    result = DistillerWorker(executor).run(ctx)

    assert result.error is None
    assert executor.call_count == 2  # só pontuou+destilou o item 0
    distilled = [p for p in result.payloads if isinstance(p, DistilledPayload)]
    scores = [p for p in result.payloads if isinstance(p, ScorePayload)]
    assert len(distilled) == 1
    assert len(scores) == 1  # itens 1 e 2 NUNCA foram pontuados, não estranhados
    stats = result.stats.model_dump()
    assert stats["scored"] == 1
    assert stats["distilled"] == 1


def test_max_score_items_limits_how_many_items_are_read() -> None:
    """`max_score_items` bound é passado pro `items_to_score` da knowledge —
    limite distinto de `max_distill_items` (AC: nomes sem ambiguidade)."""

    class _CountingKnowledge(_FakeKnowledge):
        def __init__(self, items: list[ItemView]) -> None:
            super().__init__(items)
            self.seen_limit: int | None = None

        def items_to_score(self, limit: int) -> list[ItemView]:
            self.seen_limit = limit
            return list(self._items)

    knowledge = _CountingKnowledge([])
    config = DistillerConfig(max_score_items=7, max_distill_items=3)
    executor = _FakeExecutor()
    ctx = _ctx(config, knowledge, _FakeEmbedder())

    DistillerWorker(executor).run(ctx)

    assert knowledge.seen_limit == 7
