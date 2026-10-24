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


def test_config_spoofed_loopback_host_requires_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host remoto disfarçado de loopback (`127.attacker.com`) NÃO herda root/root."""
    monkeypatch.setenv("SURREAL_URL", "wss://127.attacker.com:8000/rpc")
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


class _FakeSurreal:
    """Substituto do SDK: grava as chamadas (signin/use/close) para inspeção."""

    instances: list["_FakeSurreal"] = []
    fail_signin = False

    def __init__(self, url: str) -> None:
        self.url = url
        self.calls: list[tuple[object, ...]] = []
        _FakeSurreal.instances.append(self)

    def signin(self, payload: dict[str, str]) -> None:
        self.calls.append(("signin", payload))
        if _FakeSurreal.fail_signin:
            raise RuntimeError("auth failed")

    def use(self, namespace: str, database: str) -> None:
        self.calls.append(("use", namespace, database))

    def close(self) -> None:
        self.calls.append(("close",))


@pytest.fixture
def fake_surreal(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSurreal]:
    _FakeSurreal.instances = []
    _FakeSurreal.fail_signin = False
    monkeypatch.setattr(client, "Surreal", _FakeSurreal)
    return _FakeSurreal


def test_config_auth_level_defaults_to_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem SURREAL_AUTH_LEVEL o comportamento atual (signin root-level) não muda."""
    monkeypatch.delenv("SURREAL_AUTH_LEVEL", raising=False)
    assert client.config().auth_level == "root"


def test_config_reads_auth_level_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURREAL_AUTH_LEVEL", "database")
    assert client.config().auth_level == "database"


def test_config_rejects_unknown_auth_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valor inválido falha explícito (não cai silenciosamente em root)."""
    monkeypatch.setenv("SURREAL_AUTH_LEVEL", "namespace")
    with pytest.raises(ConfigError, match="SURREAL_AUTH_LEVEL"):
        client.config()


def test_connect_root_signin_sends_only_credentials(
    monkeypatch: pytest.MonkeyPatch, fake_surreal: type[_FakeSurreal]
) -> None:
    """Root-level: signin só com usuário/senha (forma atual); ns/db vão no `use`."""
    monkeypatch.delenv("SURREAL_AUTH_LEVEL", raising=False)
    with client.connect(client.config()):
        pass
    calls = fake_surreal.instances[0].calls
    assert calls[0] == (
        "signin",
        {"username": "root", "password": "root"},  # pragma: allowlist secret
    )
    assert calls[1] == ("use", "kubo", "kubo")


def test_connect_database_signin_is_scoped_to_namespace_and_database(
    monkeypatch: pytest.MonkeyPatch, fake_surreal: type[_FakeSurreal]
) -> None:
    """Database-level (instância central): o usuário só existe naquele ns/db, então o signin
    precisa levar namespace e database — senão o servidor não acha o usuário."""
    monkeypatch.setenv("SURREAL_AUTH_LEVEL", "database")
    monkeypatch.setenv("SURREAL_USER", "app_test")
    monkeypatch.setenv("SURREAL_PASS", "pw")  # pragma: allowlist secret
    monkeypatch.setenv("SURREAL_NS", "kubo")
    monkeypatch.setenv("SURREAL_DB", "test")
    with client.connect(client.config()):
        pass
    calls = fake_surreal.instances[0].calls
    assert calls[0] == (
        "signin",
        {
            "namespace": "kubo",
            "database": "test",
            "username": "app_test",
            "password": "pw",  # pragma: allowlist secret
        },
    )
    assert calls[1] == ("use", "kubo", "test")


def test_connect_closes_connection_when_signin_fails(
    fake_surreal: type[_FakeSurreal],
) -> None:
    fake_surreal.fail_signin = True
    with pytest.raises(RuntimeError), client.connect(client.config()):
        pass
    assert ("close",) in fake_surreal.instances[0].calls


def test_rw_config_user_comes_from_env_and_defaults_to_kubo_rw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No central o usuário de escrita é `rw_<db>`; sem a env, mantém `kubo_rw`."""
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", "rw-secret")  # pragma: allowlist secret
    monkeypatch.delenv("KUBO_RW_SURREAL_USER", raising=False)
    assert client.rw_config().user == "kubo_rw"
    monkeypatch.setenv("KUBO_RW_SURREAL_USER", "rw_test")
    assert client.rw_config().user == "rw_test"


def test_rw_config_inherits_auth_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", "rw-secret")  # pragma: allowlist secret
    monkeypatch.setenv("SURREAL_AUTH_LEVEL", "database")
    assert client.rw_config().auth_level == "database"
