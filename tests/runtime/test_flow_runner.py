"""Guardas do flow runner (unit — ADR-0016 §III/§IV/§IX).

Cobre os erros de configuração que falham ALTO antes de tocar o grafo: template
desconhecido (E4), R6 (persona sem a permissão que o worker exige), executor não
suportado, e o guard do `_persist` — um ReportPayload sem `flow_ctx` fecha o run em
erro de config, nunca costura proveniência às cegas nem crasha.
"""

from __future__ import annotations

from typing import Any

import pytest

from kubo.contracts.models import ReportPayload, WorkerManifest
from kubo.errors import ConfigError
from kubo.runtime.flow_runner import _assert_permissions, _build_executor, run_flow
from kubo.runtime.personas import Persona
from kubo.runtime.runner import _persist_report
from kubo.workers.analyst import AnalystWorker


class _FakeEmbedder:
    model = "gemini-embedding-001"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def embed(self, texts: Any) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]


def _dest() -> Any:
    from kubo.distribution.destinations import ResolvedDestination

    return ResolvedDestination(
        id="owner-telegram", name="R", kind="pessoa", channel="telegram", address="c"
    )


def test_run_flow_rejects_unknown_template() -> None:
    """Template que não existe no catálogo falha alto antes de tocar o grafo (E4)."""
    embedder = _FakeEmbedder()
    dest = _dest()
    with pytest.raises(ConfigError, match="não existe"):
        run_flow(
            db=object(),
            template_name="inexistente",
            question="q",
            embedder=embedder,  # type: ignore[arg-type]
            destination=dest,
            base_url="https://x",
        )


def test_assert_permissions_blocks_persona_without_declared_integration() -> None:
    """R6: persona sem a integração que o manifest do worker exige → ConfigError."""
    persona = Persona(name="analista", executor="api", model="m", permissions=[])
    with pytest.raises(ConfigError, match="telegram"):
        _assert_permissions(persona, AnalystWorker.manifest)


def test_assert_permissions_passes_when_superset() -> None:
    """permissions ⊇ integrations passa sem erro."""
    persona = Persona(name="analista", executor="api", model="m", permissions=["telegram", "x"])
    _assert_permissions(persona, AnalystWorker.manifest)  # não levanta


def test_build_executor_rejects_non_api() -> None:
    """Só executor `api` nesta fase; `cli` é 0015 → ConfigError."""
    persona = Persona(name="p", executor="cli", model="m")
    with pytest.raises(ConfigError, match="não suportado"):
        _build_executor(persona)


def test_persist_report_without_flow_ctx_is_config_error() -> None:
    """ReportPayload sem flow_ctx = erro de config do chamador — levanta ConfigError (o
    boundary do run_worker o mapeia para kind='config'), nunca costura às cegas."""
    payload = ReportPayload(content="corpo", consulted=[])
    with pytest.raises(ConfigError, match="flow_ctx"):
        _persist_report(object(), payload, None)


def test_manifest_is_worker_manifest() -> None:
    """Sanidade: o manifest da analista declara a integração telegram (base do R6)."""
    assert isinstance(AnalystWorker.manifest, WorkerManifest)
    assert AnalystWorker.manifest.integrations == ["telegram"]


def test_dev_mini_is_wired_with_run_resume_reject() -> None:
    """0016b: `dev-mini` tem behavior no FLOW_REGISTRY com os 3 handlers (run + gate: resume,
    reject). Template sem handler ainda falha alto (ConfigError legível, nunca KeyError cru)."""
    from kubo.runtime.flow_runner import _FLOW_REGISTRY

    behavior = _FLOW_REGISTRY["dev-mini"]
    assert behavior.run is not None
    assert behavior.resume is not None  # aprovar (→ done, sem merge)
    assert behavior.reject is not None  # rejeitar (→ fecha PR + rejected)

    # Template desconhecido ainda falha alto e legível (nunca KeyError cru), antes de tocar db.
    with pytest.raises(ConfigError, match="não existe no catálogo"):
        run_flow(db=None, template_name="ghost-template", question="x", base_url="")


def test_run_flow_accepts_worker_config_kwarg_without_breaking_existing_callers() -> None:
    """`worker_config` (sessão 0021, marco 21.3/21.4) é um novo kwarg keyword-only opcional
    (default None) de `run_flow` — puramente aditivo. Chamando com ele presente, o guard de
    template desconhecido (E4) ainda dispara a MESMA ConfigError, provando que o parâmetro
    novo não desloca a semântica dos kwargs já obrigatórios (question/base_url)."""
    with pytest.raises(ConfigError, match="não existe"):
        run_flow(
            db=object(),
            template_name="inexistente",
            question="q",
            worker_config={"since": "2026-07-16T00:00:00Z"},  # type: ignore[call-arg]
            base_url="",
        )


def test_pipeline_is_registered_with_github_releases_config_model() -> None:
    """`pipeline` (ADR-0021 §21.3/21.4) declara `config_model=GithubReleasesConfig` no
    FlowBehavior — é o que permite `build_scheduler` validar eagerly a `config` de um
    `FlowEntry` no boot, no mesmo espírito de `worker_cls.manifest.config.model_validate`
    já feito para workers."""
    from kubo.runtime.flow_runner import _FLOW_REGISTRY
    from kubo.workers.github_releases import GithubReleasesConfig

    behavior = _FLOW_REGISTRY["pipeline"]

    assert behavior.config_model is GithubReleasesConfig  # type: ignore[attr-defined]


def test_dev_mini_config_model_defaults_to_none() -> None:
    """Regressão: os 4 behaviors pré-existentes (`analysis`/`analysis-review`/`dev-mini`/
    `dev-kubo`) NÃO ganharam `config_model` de graça — só `pipeline` precisa dele (os
    outros são disparados por CLI/browser, nunca por `build_scheduler`)."""
    from kubo.runtime.flow_runner import _FLOW_REGISTRY

    assert _FLOW_REGISTRY["dev-mini"].config_model is None  # type: ignore[attr-defined]


def test_pipeline_behavior_has_no_gate_wiring() -> None:
    """`pipeline` v1 não tem gate humano (o template não declara `gates` — C6, board
    `queued→collecting→stored|failed`): resume/reject/promote continuam None, igual ao
    `analysis` sem review."""
    from kubo.runtime.flow_runner import _FLOW_REGISTRY

    behavior = _FLOW_REGISTRY["pipeline"]

    assert behavior.resume is None
    assert behavior.reject is None
    assert behavior.promote is None
