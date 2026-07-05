"""Contrato de comportamento do worker (unit, ADR-0009).

Cobre plano 0004 §4.1: manifest com schema de config como classe pydantic,
payloads que espelham a store via união discriminada, `Stats`/`ErrorInfo`
alinhados a `run.stats`/`run.error`, e `validate_worker` como a única fronteira
de validação de runtime (nunca `isinstance`/`@runtime_checkable`). Os modelos
existem mas a fronteira de segurança ainda não fecha (`extra="forbid"`,
validador numérico de `Stats`, lógica de `validate_worker`) — estes testes
devem falhar por asserção/exceção agora; ficam verdes quando a implementação
(GREEN) entrar.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from kubo.contracts.models import (
    ErrorInfo,
    ItemPayload,
    RunResult,
    SourcePayload,
    Stats,
    WorkerManifest,
)
from kubo.contracts.worker import RunContext, validate_worker
from kubo.errors import ContractError


class _FakeConfig(BaseModel):
    """Config fake usada como `type[BaseModel]` nos manifests de teste."""

    feed_url: str


# ---------------------------------------------------------------------------
# WorkerManifest
# ---------------------------------------------------------------------------


def test_manifest_valido_instancia_com_defaults() -> None:
    """Manifest com name/version/config instancia; schema_version=1 e
    integrations=[] vêm por default (ADR-0009 item II)."""
    manifest = WorkerManifest(name="feed", version="0.1.0", config=_FakeConfig)

    assert manifest.name == "feed"
    assert manifest.version == "0.1.0"
    assert manifest.schema_version == 1
    assert manifest.integrations == []
    assert manifest.config is _FakeConfig


def test_manifest_rejeita_schema_version_diferente_de_1() -> None:
    """schema_version é a versão do CONTRATO (ADR-0009), não do worker — só 1 existe."""
    with pytest.raises(ValidationError):
        WorkerManifest(
            name="feed",
            version="0.1.0",
            schema_version=2,  # type: ignore[arg-type]
            config=_FakeConfig,
        )


def test_manifest_aceita_classe_pydantic_como_config() -> None:
    """`config` é o schema de config como CLASSE pydantic (type[BaseModel]),
    não uma instância nem um dict JSON-schema (ADR-0009 item II)."""
    manifest = WorkerManifest(name="feed", version="0.1.0", config=_FakeConfig)

    assert manifest.config is _FakeConfig
    assert issubclass(manifest.config, BaseModel)


def test_manifest_rejeita_campo_extra() -> None:
    """extra="forbid" fecha o manifest: um campo com nome errado é rejeitado,
    não descartado em silêncio (ADR-0009 item I)."""
    with pytest.raises(ValidationError):
        WorkerManifest(
            name="feed",
            version="0.1.0",
            config=_FakeConfig,
            bogus_field="não deveria existir",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Payloads + RunResult
# ---------------------------------------------------------------------------


def test_source_payload_instancia_espelhando_upsert_source() -> None:
    """SourcePayload espelha os kwargs de upsert_source (kind, canonical, title)."""
    payload = SourcePayload(type="source", kind="rss", canonical="https://x/feed", title=None)

    assert payload.type == "source"
    assert payload.kind == "rss"
    assert payload.canonical == "https://x/feed"
    assert payload.title is None


def test_item_payload_embute_source_payload() -> None:
    """ItemPayload carrega a SourcePayload inline; a chave natural do item é
    source+external_id (D4, ADR-0009 item III)."""
    source = SourcePayload(type="source", kind="rss", canonical="https://x/feed")

    item = ItemPayload(
        type="item",
        source=source,
        external_id="ep-1",
        content="conteúdo bruto",
    )

    assert item.source == source
    assert item.external_id == "ep-1"
    assert item.content == "conteúdo bruto"
    assert item.url is None
    assert item.title is None
    assert item.metadata is None


def test_run_result_resolve_payloads_pela_uniao_discriminada() -> None:
    """RunResult.model_validate resolve cada payload para a classe certa pelo
    campo `type` — união discriminada, sem generics (ADR-0009 item III)."""
    result = RunResult.model_validate(
        {
            "payloads": [
                {"type": "source", "kind": "rss", "canonical": "https://x/feed"},
                {
                    "type": "item",
                    "source": {"type": "source", "kind": "rss", "canonical": "https://x/feed"},
                    "external_id": "ep-1",
                    "content": "conteúdo bruto",
                },
            ]
        }
    )

    assert isinstance(result.payloads[0], SourcePayload)
    assert isinstance(result.payloads[1], ItemPayload)


def test_payload_rejeita_campo_extra() -> None:
    """extra="forbid" no payload: campo com nome errado é rejeitado, não
    descartado — payload malformado não deve ser aceito antes de persistir
    (ADR-0009 item VIII, regra 2 de D6)."""
    with pytest.raises(ValidationError):
        SourcePayload(
            type="source",
            kind="rss",
            canonical="https://x/feed",
            bogus_field="não deveria existir",  # type: ignore[call-arg]
        )


def test_run_result_tem_defaults_vazios() -> None:
    """RunResult sem args: payloads=[], stats vazio, error=None — o caminho
    ok sem nenhuma novidade."""
    result = RunResult()

    assert result.payloads == []
    assert result.stats.model_dump() == {}
    assert result.error is None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_aceita_contadores_numericos_extras() -> None:
    """Stats é permissivo nos NOMES dos contadores — cada worker conta as
    próprias métricas (ADR-0009 item IV)."""
    stats = Stats(items_seen=10, items_written=3)  # type: ignore[call-arg]

    dumped = stats.model_dump()
    assert dumped["items_seen"] == 10
    assert dumped["items_written"] == 3


def test_stats_rejeita_valor_extra_nao_numerico() -> None:
    """Stats REJEITA valor extra que não seja numérico — fecha por tipo o
    canal de vazamento de conteúdo coletado para run.stats/log (ADR-0009
    item IV, obrigação transversal do item VIII)."""
    with pytest.raises(ValidationError):
        Stats(note="texto coletado")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ErrorInfo
# ---------------------------------------------------------------------------


def test_error_info_instancia_e_serializa_para_fail_run() -> None:
    """ErrorInfo.model_dump() bate com a forma que fail_run(error=...) espera
    (ADR-0009 item IV)."""
    error = ErrorInfo(kind="http", message="falha ao buscar feed", detail={"status": 503})

    assert error.model_dump() == {
        "kind": "http",
        "message": "falha ao buscar feed",
        "detail": {"status": 503},
    }


# ---------------------------------------------------------------------------
# validate_worker
# ---------------------------------------------------------------------------


def test_validate_worker_aceita_worker_conforme() -> None:
    """Worker com manifest válido e run(ctx) callable: validate_worker
    retorna o WorkerManifest validado (ADR-0009 item V)."""

    class _ConformingWorker:
        manifest = WorkerManifest(name="fake", version="0.1.0", config=_FakeConfig)

        def run(self, ctx: RunContext) -> RunResult:
            return RunResult()

    validated = validate_worker(_ConformingWorker())

    assert isinstance(validated, WorkerManifest)
    assert validated.name == "fake"


def test_validate_worker_rejeita_objeto_sem_manifest() -> None:
    """Objeto sem o atributo `manifest` não honra o contrato — ContractError."""

    class _NoManifestWorker:
        def run(self, ctx: RunContext) -> RunResult:
            return RunResult()

    with pytest.raises(ContractError):
        validate_worker(_NoManifestWorker())


def test_validate_worker_rejeita_manifest_invalido() -> None:
    """`manifest` que não valida como WorkerManifest (falta `name`) vira
    ContractError — a fronteira do contrato é uniforme, não a ValidationError
    crua do pydantic."""

    class _BadManifestWorker:
        manifest = {"version": "0.1.0"}  # falta name

        def run(self, ctx: RunContext) -> RunResult:
            return RunResult()

    with pytest.raises(ContractError):
        validate_worker(_BadManifestWorker())


def test_validate_worker_rejeita_run_nao_callable() -> None:
    """`run` que não é callable (ex.: atributo string) — ContractError."""

    class _RunNotCallableWorker:
        manifest = WorkerManifest(name="fake", version="0.1.0", config=_FakeConfig)
        run = "not-callable"

    with pytest.raises(ContractError):
        validate_worker(_RunNotCallableWorker())


def test_validate_worker_rejeita_assinatura_de_run_errada() -> None:
    """`run` sem o parâmetro de ctx (assinatura errada) — ContractError."""

    class _WrongSignatureWorker:
        manifest = WorkerManifest(name="fake", version="0.1.0", config=_FakeConfig)

        def run(self) -> RunResult:  # falta o parâmetro de ctx
            return RunResult()

    with pytest.raises(ContractError):
        validate_worker(_WrongSignatureWorker())


def test_validate_worker_retorna_snapshot_estavel_mesmo_com_manifest_toctou() -> None:
    """Um worker hostil cujo `manifest` é property que muda a cada leitura não
    quebra a validação: validate_worker devolve UM WorkerManifest validado, e
    esse retorno é o que vale — o runner nunca relê obj.manifest depois
    (TOCTOU, ADR-0009 item V)."""

    class _ToctouWorker:
        def __init__(self) -> None:
            self._reads = 0

        @property
        def manifest(self) -> WorkerManifest:
            self._reads += 1
            return WorkerManifest(name=f"fake-{self._reads}", version="0.1.0", config=_FakeConfig)

        def run(self, ctx: RunContext) -> RunResult:
            return RunResult()

    worker = _ToctouWorker()

    validated = validate_worker(worker)
    outra_leitura = worker.manifest

    assert isinstance(validated, WorkerManifest)
    # a property muda a cada leitura — prova que o retorno de validate_worker
    # é o snapshot que vale, não uma releitura futura de obj.manifest.
    assert outra_leitura.name != validated.name
