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


def test_run_flow_rejects_dev_mini_without_behavior() -> None:
    """Estado intermediário limpo (0016 parte A): `dev-mini` existe no catálogo mas ainda
    não tem behavior no FLOW_REGISTRY (o wiring é 0016b). Disparar dá ConfigError LEGÍVEL,
    nunca KeyError cru do registry — a falha é antes de tocar db/embedder."""
    with pytest.raises(ConfigError, match="sem handler"):
        run_flow(
            db=None,
            template_name="dev-mini",
            question="x",
            embedder=None,  # type: ignore[arg-type]
            destination=None,  # type: ignore[arg-type]
            base_url="",
            executor=None,
        )
