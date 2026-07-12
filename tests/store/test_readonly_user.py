"""Prova fail-closed do usuário read-only da UI (ADR-0014 amendment 0010, E5c).

O kubo-api roda com um usuário ROOT-level VIEWER (Path A da sonda M7a): signin igual
ao do root (sem ns/db), então SEM ramo no caminho strict `client.py`. Este teste prova
o invariante que sustenta a decisão: como o viewer, LER funciona e ESCREVER não muda
o dado. Tolerante ao mecanismo de negação — no v3.1.5 a escrita negada retorna vazio
sem levantar (quirk pinado no ADR), mas um bump futuro pode passar a levantar; o teste
asserta o ESTADO (dado intacto), não o silêncio.

Criação do usuário aqui é fixture de teste (efêmera, removida no teardown) — em
produção é passo one-time do runbook, NUNCA migration (senha em .surql fura o inv. 8).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from dataclasses import replace
from typing import Any

import pytest

from kubo.store import client, knowledge, migrations

pytestmark = pytest.mark.integration

_RO_DB = "test_readonly"
_RO_USER = "kubo_ro_test"
_RO_PASS = "readonly-ephemeral-test-pw"  # pragma: allowlist secret  # container efêmero, descartado


@pytest.fixture
def ro_env() -> Iterator[tuple[Any, Any]]:
    """Sobe um db efêmero com schema + um `run` semeado, define um usuário ROOT-level
    VIEWER e entrega (conexão root de escrita, conexão do viewer). Remove o usuário e o
    db no teardown — nada vaza para o servidor compartilhado."""
    root_cfg = replace(client.config(), database=_RO_DB)
    viewer_cfg = replace(root_cfg, user=_RO_USER, password=_RO_PASS)
    with client.connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_RO_DB};")
        root.use(root_cfg.namespace, root_cfg.database)
        migrations.apply_migrations(root)
        knowledge.start_run(root, worker="feed")  # algo para o viewer LER
        # ROOT-level VIEWER: mesma forma de signin do root (sem ns/db) — Path A.
        root.query(f"DEFINE USER {_RO_USER} ON ROOT PASSWORD '{_RO_PASS}' ROLES VIEWER;")
        try:
            with client.connect(viewer_cfg) as viewer:
                yield root, viewer
        finally:
            root.query(f"REMOVE USER IF EXISTS {_RO_USER} ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_RO_DB};")


def test_readonly_user_can_read(ro_env: tuple[Any, Any]) -> None:
    """O viewer LÊ — prova que a credencial funciona (não está só quebrada)."""
    _root, viewer = ro_env
    rows = viewer.query("SELECT worker, status FROM run;")
    assert rows and rows[0]["worker"] == "feed"


def test_readonly_user_cannot_write(ro_env: tuple[Any, Any]) -> None:
    """Fail-closed: uma tentativa de escrita do viewer NÃO altera o dado. Read-back
    como root prova o estado intacto. Tolera vazio OU exceção (não pina o quirk)."""
    root, viewer = ro_env
    before = root.query("SELECT VALUE status FROM run;")[0]

    with suppress(Exception):
        viewer.query("UPDATE run SET status = 'error';")
    with suppress(Exception):
        viewer.query("CREATE run SET worker = 'intruder', status = 'running';")

    after_status = root.query("SELECT VALUE status FROM run;")
    after_count = root.query("SELECT count() FROM run GROUP ALL;")[0]["count"]
    assert after_status == [before]  # status não mudou
    assert after_count == 1  # nenhum run criado


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-m", "integration"]))
