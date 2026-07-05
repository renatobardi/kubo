"""Client mínimo de conexão ao SurrealDB — única porta de entrada ao datastore.

Config vem só de ambiente (invariante 8: segredo por referência, nunca hardcoded).
Defaults servem dev local e CI (container efêmero em loopback).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from surrealdb import Surreal

from kubo.errors import ConfigError

_DEFAULT_URL = "ws://127.0.0.1:8000/rpc"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Config:
    """Parâmetros de conexão. `password` é redigido em repr/str.

    CUIDADO: `__repr__` protege log/traceback do objeto, mas NÃO cobre
    serialização explícita (`dataclasses.asdict`, `vars`, JSON) — nunca serialize
    um Config nem o passe como contexto de log estruturado.
    """

    url: str
    user: str
    password: str
    namespace: str
    database: str

    def __repr__(self) -> str:
        return (
            f"Config(url={self.url!r}, user={self.user!r}, password=***, "
            f"namespace={self.namespace!r}, database={self.database!r})"
        )


def _is_loopback(url: str) -> bool:
    """True se o host do URL é loopback — onde os defaults root/root são aceitáveis."""
    host = urlparse(url).hostname or ""
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def config() -> Config:
    """Lê a config de conexão do ambiente.

    Defaults root/root SÓ valem para endpoint loopback (dev/CI). Para host remoto,
    `SURREAL_USER`/`SURREAL_PASS` são obrigatórios — senão a conexão falharia-aberta
    com credencial default contra um endpoint real. Falha explícita (ConfigError).
    """
    url = os.environ.get("SURREAL_URL", _DEFAULT_URL)
    user = os.environ.get("SURREAL_USER")
    password = os.environ.get("SURREAL_PASS")
    if _is_loopback(url):
        user = user or "root"
        password = password or "root"
    elif user is None or password is None:
        raise ConfigError(
            "SURREAL_USER e SURREAL_PASS são obrigatórios para endpoint não-loopback "
            f"({url!r}) — não caia no default root/root contra um host real."
        )
    return Config(
        url=url,
        user=user,
        password=password,
        namespace=os.environ.get("SURREAL_NS", "kubo"),
        database=os.environ.get("SURREAL_DB", "kubo"),
    )


@contextmanager
def connect(cfg: Config | None = None) -> Generator[Any]:
    """Abre uma conexão autenticada e com ns/db selecionados; fecha ao sair.

    `Any` no yield é deliberado: o tipo concreto do SDK varia por scheme de URL
    (ws/http/embedded) e sua API de query é dinâmica. A store encapsula esse
    acesso — nenhum consumidor fora de `kubo/store/` toca a conexão crua.
    """
    cfg = cfg or config()
    db = Surreal(cfg.url)
    db.signin({"username": cfg.user, "password": cfg.password})
    db.use(cfg.namespace, cfg.database)
    try:
        yield db
    finally:
        db.close()
