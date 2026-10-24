"""Signin de DATABASE-level — o modelo da instância SurrealDB central (oute-surreal-db).

Lá cada projeto vive num namespace e cada ambiente numa database; os usuários da aplicação são
de DATABASE (`app_<db>` OWNER, `rw_<db>` EDITOR, `ro_<db>` VIEWER) e NÃO existem no nível ROOT,
para que a credencial do kubo nunca enxergue o namespace de outro projeto. O signin precisa levar
namespace + database (`auth_level="database"`). As senhas são geradas por run (invariante 8).
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb.errors import NotAllowedError

from kubo.store import client

_DB = "test_database_auth"
_PASS = secrets.token_urlsafe(24)  # nunca um literal no repo


@pytest.fixture
def scoped_users() -> Iterator[tuple[Any, client.Config]]:
    """Cria uma database efêmera com 3 usuários de DATABASE; entrega (conexão root, cfg base)."""
    root_cfg = replace(client.config(), database=_DB)
    with client.connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        root.use(root_cfg.namespace, _DB)
        for user, role in (("app_t", "OWNER"), ("rw_t", "EDITOR"), ("ro_t", "VIEWER")):
            root.query(f"DEFINE USER {user} ON DATABASE PASSWORD '{_PASS}' ROLES {role};")
        try:
            yield root, replace(root_cfg, password=_PASS, auth_level="database")
        finally:
            root.query(f"REMOVE DATABASE IF EXISTS {_DB};")


@pytest.mark.integration
def test_database_level_user_connects_and_writes(scoped_users: tuple[Any, client.Config]) -> None:
    """OWNER e EDITOR de database conectam via signin com ns/db e gravam; o read-back é feito
    COMO ROOT (se a escrita fosse um no-op silencioso, o root não veria nada)."""
    root, cfg = scoped_users
    for user, key in (("app_t", 1), ("rw_t", 2)):
        with client.connect(replace(cfg, user=user)) as db:
            db.query(f"CREATE probe:{key} SET by = '{user}';")
    rows = root.query("SELECT VALUE by FROM probe ORDER BY id;")
    assert rows == ["app_t", "rw_t"]


@pytest.mark.integration
def test_viewer_connects_but_cannot_write(scoped_users: tuple[Any, client.Config]) -> None:
    root, cfg = scoped_users
    with client.connect(replace(cfg, user="ro_t")) as db:
        db.query("CREATE probe:9 SET by = 'viewer';")
    assert root.query("SELECT * FROM probe;") == []


@pytest.mark.integration
def test_root_level_signin_does_not_find_a_database_user(
    scoped_users: tuple[Any, client.Config],
) -> None:
    """Sem namespace/database no signin o servidor não acha o usuário: é exatamente o modo de
    falha do kubo antigo (root-level) contra a instância central."""
    _, cfg = scoped_users
    with (
        pytest.raises(NotAllowedError),
        client.connect(replace(cfg, user="app_t", auth_level="root")),
    ):
        pass


@pytest.mark.integration
def test_database_user_is_denied_in_another_database(
    scoped_users: tuple[Any, client.Config],
) -> None:
    """O usuário de uma database não entra em outra (isolamento entre ambientes/projetos)."""
    _, cfg = scoped_users
    with (
        pytest.raises(NotAllowedError),
        client.connect(replace(cfg, user="app_t", database="outra_database")),
    ):
        pass
