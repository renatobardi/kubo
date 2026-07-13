"""Contrato de comportamento da CLI `kubo` (ADR-0013 §8.5, Marco 8.5).

Cobre a camada pura (`parse_distilled_id`, `dedupe_hits`, `format_query_results`,
`format_distilled` — sem DB) e a camada de orquestração (`run_query`, `run_show`
— integração, SurrealDB real, embedder sempre FAKE, nenhuma chamada de rede).
`kubo.__main__` ainda é STUB (NotImplementedError): estes testes devem falhar
por isso agora; ficam verdes quando a implementação (GREEN) entrar.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.__main__ import (
    _build_parser,
    _handle_flow,
    _sanitize,
    dedupe_hits,
    format_distilled,
    format_query_results,
    main,
    parse_distilled_id,
    run_flow_command,
    run_query,
    run_show,
)
from kubo.distribution.destinations import ResolvedDestination
from kubo.errors import ConfigError
from kubo.runtime.flow_runner import FlowRunResult
from kubo.store import client, knowledge, migrations
from kubo.store.knowledge import Chunk, DistilledView, ProvenanceItem, RunRef, SearchHit

_CLI_DB = "test_cli"
_DIM = 768
_MODEL = "gemini-embedding-001"
_TASK_TYPE = "SEMANTIC_SIMILARITY"


class FakeEmbedder:
    """Embedder determinístico sem rede: sempre devolve o mesmo vetor, para
    todo texto de entrada — os testes controlam a proximidade via o vetor
    passado no construtor, não via conteúdo do texto."""

    model = "fake"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector for _ in texts]


def _vec(*nonzero: float, dim: int = _DIM) -> list[float]:
    """Vetor de `dim` posições: os valores de `nonzero` nas primeiras posições,
    resto zero — mesmo helper de tests/store/test_knowledge.py, para montar
    pares próximos/ortogonais sem hardcodar 768 números."""
    values = [float(v) for v in nonzero] + [0.0] * (dim - len(nonzero))
    return values[:dim]


def _chunk(seq: int, embedding: list[float], text: str = "trecho") -> Chunk:
    """Chunk válido (768 dims) com a tripla de proveniência do ADR-0006."""
    return Chunk(
        text=text, seq=seq, embedding=embedding, model=_MODEL, dim=_DIM, task_type=_TASK_TYPE
    )


def _view(
    *, items: list[ProvenanceItem] | None = None, runs: list[RunRef] | None = None
) -> DistilledView:
    """DistilledView fabricado para os testes puros de formatação (sem DB)."""
    return DistilledView(
        id=RecordID("distilled", "d1"),
        summary="resumo destilado",
        claims=["afirmação A", "afirmação B"],
        items=items if items is not None else [],
        runs=runs if runs is not None else [],
        entities=[],
    )


# ---------------------------------------------------------------------------
# Camada pura: parse_distilled_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_key"),
    [
        ("distilled:abc", "abc"),
        ("abc", "abc"),
    ],
)
def test_parse_distilled_id_accepts_bare_key_or_distilled_prefix(
    raw: str, expected_key: str
) -> None:
    """Com ou sem o prefixo `distilled:`, resolve ao mesmo RecordID da tabela `distilled`."""
    assert parse_distilled_id(raw) == RecordID("distilled", expected_key)


@pytest.mark.parametrize("raw", ["item:x", "source:x", "", "   "])
def test_parse_distilled_id_rejects_other_tables_and_empty_input(raw: str) -> None:
    """Nunca deixa o argv escolher a tabela (segurança): prefixo de outra tabela
    ou entrada vazia/whitespace levanta ValueError, não silenciosamente aceita."""
    with pytest.raises(ValueError):
        parse_distilled_id(raw)


# ---------------------------------------------------------------------------
# Camada pura: _sanitize
# ---------------------------------------------------------------------------


def test_sanitize_remove_control_chars_mas_preserva_texto_e_newline_tab() -> None:
    """`_sanitize` remove caracteres de controle (ESC, BEL) mas preserva o texto
    normal, `\\n` e `\\t` — defesa contra terminal injection em conteúdo coletado
    hostil que vira summary/claim/título impresso pela CLI (Major, segurança)."""
    result = _sanitize("a\x1b[31mb\x07c\nd\t")

    assert "\x1b" not in result
    assert "\x07" not in result
    for kept in ("a", "b", "c", "\n", "d", "\t"):
        assert kept in result


# ---------------------------------------------------------------------------
# Camada pura: dedupe_hits
# ---------------------------------------------------------------------------


def test_dedupe_hits_keeps_the_minimum_score_per_distilled_ordered_ascending() -> None:
    """Dois chunks do mesmo distilled colapsam num hit só — o de MENOR score
    (mais perto), não o primeiro da lista de entrada (que está fora de ordem
    de propósito, para provar que o dedupe não é 'mantém o primeiro visto')."""
    d1 = RecordID("distilled", "d1")
    d2 = RecordID("distilled", "d2")
    hits = [
        SearchHit(distilled=d1, chunk=RecordID("chunk", "c1"), score=0.5),
        SearchHit(distilled=d2, chunk=RecordID("chunk", "c2"), score=0.3),
        SearchHit(distilled=d1, chunk=RecordID("chunk", "c3"), score=0.2),
    ]

    result = dedupe_hits(hits)

    assert [(h.distilled, h.score) for h in result] == [(d1, 0.2), (d2, 0.3)]


# ---------------------------------------------------------------------------
# Camada pura: format_query_results / format_distilled
# ---------------------------------------------------------------------------


def test_format_query_results_includes_distilled_id_and_summary() -> None:
    """O id do distilled aparece no output — é o insumo do `kubo show`, sem ele
    os dois comandos não se compõem — junto com o summary."""
    d1 = RecordID("distilled", "d1")
    hit = SearchHit(distilled=d1, chunk=RecordID("chunk", "c1"), score=0.12)
    view = _view()

    output = format_query_results([(hit, view)])

    assert "d1" in output
    assert "resumo destilado" in output


def test_format_distilled_without_provenance_has_summary_and_claims_but_not_source() -> None:
    """Sem `provenance`: summary e claims aparecem; a cadeia de origem (source
    canonical) NÃO aparece — provenance=False é sucinto por design."""
    output = format_distilled(_view(), provenance=False)

    assert "resumo destilado" in output
    assert "afirmação A" in output
    assert "afirmação B" in output
    assert "https://x/feed" not in output


def test_format_distilled_with_provenance_adds_source_url_and_worker() -> None:
    """Com `provenance=True`: soma a cadeia item->source (canonical/url) e o
    worker do run que produziu o destilado."""
    item = ProvenanceItem(
        external_id="ep-1",
        url="https://x/ep-1",
        title="Episódio 1",
        source_canonical="https://x/feed",
        source_title="Feed X",
        source_kind="rss",
    )
    run = RunRef(worker="scribe", status="ok")

    output = format_distilled(_view(items=[item], runs=[run]), provenance=True)

    assert "https://x/feed" in output
    assert "https://x/ep-1" in output
    assert "scribe" in output


# ---------------------------------------------------------------------------
# Camada de orquestração: integração (SurrealDB real, embedder FAKE)
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do
    zero (mesmo padrão de tests/store/test_knowledge.py)."""
    cfg = replace(client.config(), database=_CLI_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_CLI_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_CLI_DB};")


@pytest.mark.integration
def test_run_query_orders_results_by_real_proximity(db: Any) -> None:
    """O KNN roda de verdade — o fake NÃO mascara a ordenação: o distilled cujo
    chunk é IDÊNTICO ao vetor da pergunta aparece antes do ortogonal."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_a = knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    item_b = knowledge.upsert_item(db, source=source_id, external_id="b", content="B")
    knowledge.insert_distilled(
        db, item=item_a, summary="resumo A perto", chunks=[_chunk(0, _vec(1.0))]
    )
    knowledge.insert_distilled(
        db, item=item_b, summary="resumo B longe", chunks=[_chunk(0, _vec(0.0, 1.0))]
    )
    fake = FakeEmbedder(_vec(1.0))

    output = run_query(db, fake, "qualquer pergunta", k=2)

    assert output.index("resumo A perto") < output.index("resumo B longe")


@pytest.mark.integration
def test_run_query_deduplicates_hits_from_the_same_distilled(db: Any) -> None:
    """Um distilled com 2 chunks perto do vetor da pergunta aparece UMA vez na
    saída — dois chunks do mesmo destilado não duplicam o summary."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="a", content="A")
    knowledge.insert_distilled(
        db,
        item=item_id,
        summary="resumo único",
        chunks=[_chunk(0, _vec(1.0)), _chunk(1, _vec(0.9, 0.1))],
    )
    fake = FakeEmbedder(_vec(1.0))

    output = run_query(db, fake, "qualquer pergunta", k=5)

    assert output.count("resumo único") == 1


@pytest.mark.integration
def test_run_show_with_provenance_contains_full_chain(db: Any) -> None:
    """`run_show(..., provenance=True)` traz summary + a cadeia item->source
    (canonical/url) + o worker do run — a proveniência completa."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed", title="Feed X")
    item_id = knowledge.upsert_item(
        db,
        source=source_id,
        external_id="ep-1",
        content="bruto",
        url="https://x/ep-1",
        title="Episódio 1",
    )
    run_id = knowledge.start_run(db, worker="scribe")
    distilled_id = knowledge.insert_distilled(
        db, item=item_id, summary="resumo destilado", chunks=[], run=run_id
    )

    output = run_show(db, str(distilled_id), provenance=True)

    assert output is not None
    assert "resumo destilado" in output
    assert "https://x/feed" in output
    assert "https://x/ep-1" in output
    assert "scribe" in output


@pytest.mark.integration
def test_run_show_without_provenance_omits_source_canonical(db: Any) -> None:
    """Sem `provenance`, o canonical da source não aparece — sucinto por design,
    mesma regra de `format_distilled`."""
    source_id = knowledge.upsert_source(db, kind="rss", canonical="https://x/feed")
    item_id = knowledge.upsert_item(db, source=source_id, external_id="ep-1", content="bruto")
    distilled_id = knowledge.insert_distilled(db, item=item_id, summary="resumo simples", chunks=[])

    output = run_show(db, str(distilled_id), provenance=False)

    assert output is not None
    assert "resumo simples" in output
    assert "https://x/feed" not in output


@pytest.mark.integration
def test_run_show_returns_none_for_nonexistent_distilled(db: Any) -> None:
    """Um id de distilled que não existe no grafo devolve None — não levanta e
    não confunde 'sem proveniência' com 'destilado inexistente'."""
    assert run_show(db, "distilled:nao-existe", provenance=False) is None


# ---------------------------------------------------------------------------
# main(): caminho sem GEMINI_API_KEY
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_main_query_without_gemini_api_key_exits_with_code_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sem GEMINI_API_KEY, `main` constrói o embedder via `GeminiEmbedder.from_env()`,
    que levanta ConfigError (invariante 8: key só por env) — `main` captura e
    sai com código 2, com mensagem clara, sem traceback vazando pro usuário.
    Marcado integration: a ordem exata entre construir o embedder e abrir a
    conexão com o banco é decisão do GREEN, então garantimos o SurrealDB de pé
    para não travar o teste numa suposição de implementação."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = main(["query", "pergunta qualquer"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "GEMINI_API_KEY" in (captured.err + captured.out)


# ---------------------------------------------------------------------------
# flow run (ADR-0016) — parser + wiring do CLI (unit, sem DB nem rede)
# ---------------------------------------------------------------------------


def _flow_result(state: str) -> FlowRunResult:
    return FlowRunResult(
        flow=RecordID("flow", "1"),
        task=RecordID("task", "1"),
        run=RecordID("run", "1"),
        state=state,
    )


def test_parser_parses_flow_run() -> None:
    """`flow run <template> <pergunta>` roteia, com destino default owner-telegram."""
    args = _build_parser().parse_args(["flow", "run", "analysis", "o que é X?"])
    assert args.command == "flow"
    assert args.flow_command == "run"
    assert args.template == "analysis"
    assert args.question == "o que é X?"
    assert args.destination == "owner-telegram"


def test_handle_flow_without_run_subcommand_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    """`kubo flow` sem `run` imprime uso e sai 2."""
    args = argparse.Namespace(flow_command=None)
    assert _handle_flow(None, args) == 2
    assert "uso:" in capsys.readouterr().err


def test_handle_flow_maps_state_to_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """delivered → exit 0; failed → exit 1; ambos imprimem o id do flow e o estado."""
    monkeypatch.setattr("kubo.__main__.run_flow_command", lambda *a, **k: _flow_result("delivered"))
    args = argparse.Namespace(
        flow_command="run", template="analysis", question="q", destination="owner-telegram"
    )
    assert _handle_flow(object(), args) == 0
    assert "delivered" in capsys.readouterr().out

    monkeypatch.setattr("kubo.__main__.run_flow_command", lambda *a, **k: _flow_result("failed"))
    assert _handle_flow(object(), args) == 1


def test_run_flow_command_rejects_unknown_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    """Destino inexistente em destinations.yaml falha alto (ConfigError), antes de embeddar."""
    monkeypatch.setattr(
        "kubo.__main__.resolve_destinations",
        lambda ds: [
            ResolvedDestination(
                id="owner-telegram", name="R", kind="pessoa", channel="telegram", address="c"
            )
        ],
    )
    with pytest.raises(ConfigError, match="não existe"):
        run_flow_command(object(), template="analysis", question="q", destination_id="fantasma")
