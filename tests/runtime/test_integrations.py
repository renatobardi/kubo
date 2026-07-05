"""Loader do catálogo `integrations` + resolução de segredo + negação (unit).

Cobre plano 0004 §4.3: schema pydantic da integração (auth só por REFERÊNCIA a
env — valor inline rejeitado), resolução de segredo pelo RUNTIME (worker nunca
lê `os.environ`), e a negação que acontece na montagem do ctx (declaradas ∩
existentes injetadas, o resto negado). Sem SurrealDB — tudo unit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kubo.errors import ConfigError
from kubo.runtime.integrations import (
    Integration,
    ResolvedIntegration,
    load_integration,
    load_integrations,
    resolve_integrations,
)

_CATALOG = Path(__file__).parents[2] / "catalogs" / "integrations"


def test_load_rss_from_real_catalog() -> None:
    """O `rss.yaml` versionado carrega como Integration pública (auth none)."""
    integ = load_integration(_CATALOG / "rss.yaml")

    assert integ.name == "rss"
    assert integ.kind == "http"
    assert integ.auth.type == "none"
    assert integ.auth.secret_ref is None


def test_load_integrations_indexes_by_name() -> None:
    """load_integrations devolve {name: Integration}; o catálogo real tem rss."""
    catalog = load_integrations(_CATALOG)

    assert "rss" in catalog
    assert isinstance(catalog["rss"], Integration)


def test_loader_rejects_inline_secret(tmp_path: Path) -> None:
    """auth com secret_ref que NÃO é referência env:VAR (valor inline) é
    rejeitado — segredo só por referência (invariante 8, plano §4.3.1)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: bad\nkind: http\nauth:\n  type: bearer\n  secret_ref: inline-value-not-a-ref\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="inline|env:"):
        load_integration(bad)


def test_auth_none_forbids_secret_ref(tmp_path: Path) -> None:
    """auth.type=none não pode carregar secret_ref — incoerência rejeitada."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: bad\nkind: http\nauth:\n  type: none\n  secret_ref: env:FOO\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_integration(bad)


def test_auth_bearer_requires_secret_ref(tmp_path: Path) -> None:
    """auth.type=bearer exige secret_ref (env:VAR) — credencial declarada."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\nkind: http\nauth:\n  type: bearer\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_integration(bad)


def test_loader_rejects_unknown_field(tmp_path: Path) -> None:
    """Campo desconhecido no YAML é rejeitado (extra=forbid) — não silenciado."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: bad\nkind: http\nauth:\n  type: none\nbogus: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_integration(bad)


def _bearer_catalog() -> dict[str, Integration]:
    """Catálogo de um integração bearer que referencia env:KUBO_TEST_TOKEN."""
    return {
        "svc": Integration.model_validate(
            {
                "name": "svc",
                "kind": "http",
                "auth": {
                    "type": "bearer",
                    "secret_ref": "env:KUBO_TEST_TOKEN",  # pragma: allowlist secret
                },
            }
        )
    }


def test_resolve_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """O RUNTIME resolve a referência env:VAR na montagem do ctx; o valor
    resolvido vive só no objeto (worker nunca lê os.environ, plano §4.3.2)."""
    monkeypatch.setenv("KUBO_TEST_TOKEN", "resolved-value")

    resolved = resolve_integrations(["svc"], _bearer_catalog())

    assert isinstance(resolved["svc"], ResolvedIntegration)
    assert resolved["svc"].secret == "resolved-value"  # pragma: allowlist secret
    assert resolved["svc"].auth_type == "bearer"


def test_resolve_missing_env_var_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """secret_ref cuja variável de ambiente não existe falha alto (ConfigError),
    não injeta credencial vazia."""
    monkeypatch.delenv("KUBO_TEST_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="KUBO_TEST_TOKEN"):
        resolve_integrations(["svc"], _bearer_catalog())


def test_resolve_injects_only_declared() -> None:
    """Só as integrações DECLARADAS entram no ctx; o resto do catálogo é negado
    por omissão (least-privilege, plano §4.3.3)."""
    catalog = load_integrations(_CATALOG)  # tem 'rss'

    resolved = resolve_integrations([], catalog)

    assert resolved == {}


def test_resolve_denies_nonexistent_declared() -> None:
    """Integração declarada que não existe no catálogo é negada com falha alta:
    manifest válido ≠ permissão concedida (plano §4.3.3)."""
    with pytest.raises(ConfigError, match="ghost"):
        resolve_integrations(["ghost"], load_integrations(_CATALOG))


def test_resolved_integration_from_public_source() -> None:
    """Integração pública (auth none) resolve com secret=None — sem env exigido."""
    resolved = resolve_integrations(["rss"], load_integrations(_CATALOG))

    assert resolved["rss"].secret is None
    assert resolved["rss"].auth_type == "none"


def test_resolved_secret_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """O segredo resolvido NUNCA aparece no repr — fechado POR CONSTRUÇÃO
    (field repr=False), não por disciplina de não-logar. Um worker que faz
    `raise RuntimeError(ctx.integrations['svc'])` não exfiltra o segredo pelo
    caminho de erro do runner (fecha o achado crítico #1 da revisão)."""
    monkeypatch.setenv("KUBO_TEST_TOKEN", "super-secret-value")

    resolved = resolve_integrations(["svc"], _bearer_catalog())

    assert "super-secret-value" not in repr(resolved["svc"])
    assert "super-secret-value" not in str(resolved["svc"])


def test_inline_secret_value_not_echoed_in_error(tmp_path: Path) -> None:
    """Ao rejeitar um secret_ref inline, a mensagem de erro NÃO ecoa o valor —
    colar um token real por engano não o vaza para o ConfigError/run.error
    (fecha o achado crítico #2 da revisão)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: bad\nkind: http\nauth:\n  type: bearer\n  secret_ref: sk-pasted-real-token\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_integration(bad)

    assert "sk-pasted-real-token" not in str(exc_info.value)
