"""Testes do client mínimo de conexão ao SurrealDB (kubo.store.client)."""

import pytest

from kubo.errors import ConfigError
from kubo.store import client


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem env, o config cai nos defaults de dev/CI (loopback, ns/db kubo)."""
    for var in ("SURREAL_URL", "SURREAL_USER", "SURREAL_PASS", "SURREAL_NS", "SURREAL_DB"):
        monkeypatch.delenv(var, raising=False)
    cfg = client.config()
    assert cfg.url == "ws://127.0.0.1:8000/rpc"
    assert cfg.namespace == "kubo"
    assert cfg.database == "kubo"
    assert cfg.user == "root"


def test_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env sobrepõe os defaults — segredo (pass) só por env, nunca hardcoded."""
    monkeypatch.setenv("SURREAL_URL", "wss://db:8000/rpc")
    monkeypatch.setenv("SURREAL_USER", "kubo_app")
    monkeypatch.setenv("SURREAL_PASS", "s3cret")  # pragma: allowlist secret
    monkeypatch.setenv("SURREAL_NS", "prod")
    monkeypatch.setenv("SURREAL_DB", "main")
    cfg = client.config()
    assert cfg.url == "wss://db:8000/rpc"
    assert cfg.user == "kubo_app"
    assert cfg.password == "s3cret"  # pragma: allowlist secret
    assert cfg.namespace == "prod"
    assert cfg.database == "main"


def test_config_repr_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """repr/str não pode vazar o segredo (proibição de logar segredos)."""
    monkeypatch.setenv("SURREAL_PASS", "topsecret")  # pragma: allowlist secret
    cfg = client.config()
    assert "topsecret" not in repr(cfg)
    assert "topsecret" not in str(cfg)


def test_config_remote_url_requires_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint não-loopback sem credencial explícita falha (não cai em root/root)."""
    monkeypatch.setenv("SURREAL_URL", "wss://db.internal:8000/rpc")
    monkeypatch.delenv("SURREAL_USER", raising=False)
    monkeypatch.delenv("SURREAL_PASS", raising=False)
    with pytest.raises(ConfigError):
        client.config()


def test_config_remote_url_with_credentials_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint remoto COM credencial explícita é aceito."""
    monkeypatch.setenv("SURREAL_URL", "wss://db.internal:8000/rpc")
    monkeypatch.setenv("SURREAL_USER", "kubo_app")
    monkeypatch.setenv("SURREAL_PASS", "s3cret")  # pragma: allowlist secret
    cfg = client.config()
    assert cfg.url == "wss://db.internal:8000/rpc"
    assert cfg.user == "kubo_app"
