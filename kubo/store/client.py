"""Client mínimo de conexão ao SurrealDB — única porta de entrada ao datastore.

Config vem só de ambiente (invariante 8: segredo por referência, nunca hardcoded).
Defaults servem dev local e CI (container efêmero em loopback).
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

from surrealdb import Surreal

from kubo.errors import ConfigError

_DEFAULT_URL = "ws://127.0.0.1:8000/rpc"
# Usuário de ESCRITA da UI (ROOT-level EDITOR). Nome fixo (não é segredo); a senha vem por
# env, rotação idêntica ao kubo_ro. ADR-0018 §I.
_RW_USER = "kubo_rw"


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
    """True se o host do URL é loopback — onde os defaults root/root são aceitáveis.

    Usa `ipaddress` (não prefixo de string): `127.attacker.com` NÃO é loopback —
    senão um host remoto controlado pelo atacante herdaria o default root/root.
    """
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
def connect(cfg: Config | None = None) -> Generator[Any, None, None]:
    """Abre uma conexão autenticada e com ns/db selecionados; fecha ao sair.

    `Any` no yield é deliberado: o tipo concreto do SDK varia por scheme de URL
    (ws/http/embedded) e sua API de query é dinâmica. A store encapsula esse
    acesso — nenhum consumidor fora de `kubo/store/` toca a conexão crua.
    """
    cfg = cfg or config()
    db = Surreal(cfg.url)
    # signin/use DENTRO do try: se qualquer um falhar, o finally ainda fecha a
    # conexão (senão vaza socket a cada falha de auth).
    try:
        db.signin({"username": cfg.user, "password": cfg.password})
        db.use(cfg.namespace, cfg.database)
        yield db
    finally:
        db.close()


def rw_config() -> Config:
    """Config de ESCRITA (kubo_rw, ROLES EDITOR — ADR-0018 §I). Herda url/ns/db da config base
    (MESMO endpoint que o kubo_ro), troca só user→kubo_rw e password→`KUBO_RW_SURREAL_PASS`.

    Fail-fast: a env ausente levanta ConfigError — os 2 handlers de escrita traduzem em 503, e
    o resto da UI (kubo_ro) segue vivo. A senha nunca tem default (invariante 8)."""
    password = os.environ.get("KUBO_RW_SURREAL_PASS")
    if not password:
        raise ConfigError(
            "KUBO_RW_SURREAL_PASS ausente — a escrita da UI (kubo_rw) está indisponível. "
            "Crie o usuário pelo runbook e defina a env (invariante 8: segredo por referência)."
        )
    return replace(config(), user=_RW_USER, password=password)


@contextmanager
def connect_rw() -> Generator[Any, None, None]:
    """Conexão de ESCRITA por-request (kubo_rw, EDITOR). Mesma forma de signin do root/kubo_ro
    (Path A — zero branch no caminho de conexão). Chamada EXCLUSIVAMENTE dentro dos handlers
    POST de escrita da UI (as 2 ações do D38), nunca em app state (ADR-0018 §I)."""
    with connect(rw_config()) as db:
        yield db
