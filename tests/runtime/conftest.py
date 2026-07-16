"""Fixtures/helpers compartilhados pelos verticals do flow dev (dev-mini/dev-kubo).

Extraído de `test_flow_dev_vertical.py`/`test_flow_dev_kubo_vertical.py` (SonarCloud
flagou duplicação de novo código entre os dois — mesmo CliRunner fake, mesmo
neutralizador de gitops, mesmo helper de gate de promoção, mesmo corpo de fixture de
DB variando só o nome). Os dois arquivos continuam com seu próprio `db`/`_run_to_gate`
(a lógica de disparo do flow difere de verdade entre os alvos), só o que era cópia
literal migrou pra cá.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import replace
from typing import Any

import pytest

from kubo.executors.cli import CliOutcome
from kubo.store import client, migrations
from kubo.workers import gitops


@pytest.fixture
def make_db() -> Iterator[Callable[[str], Any]]:
    """Fábrica de conexão de teste: `make_db(name)` cria/limpa um database próprio,
    aplica as migrations do zero e remove no teardown. Cada vertical chama com seu
    próprio nome (`_DB` do módulo) — evita dois testes disputando o mesmo database."""
    with ExitStack() as stack:
        opened: list[tuple[Any, str]] = []

        def factory(name: str) -> Any:
            cfg = replace(client.config(), database=name)
            conn = stack.enter_context(client.connect(cfg))
            conn.query(f"REMOVE DATABASE IF EXISTS {name};")
            conn.use(cfg.namespace, cfg.database)
            migrations.apply_migrations(conn)
            opened.append((conn, name))
            return conn

        yield factory
        for conn, name in opened:
            conn.query(f"REMOVE DATABASE IF EXISTS {name};")


class FakeCli:
    """CliRunner fake: devolve um CliOutcome preset (a prosa untrusted do agente)."""

    def __init__(self, outcome: CliOutcome) -> None:
        self._outcome = outcome

    def run(self, prompt: str, *, workspace: str) -> CliOutcome:
        return self._outcome


def fake_gitops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutraliza o git real: clone/identity/branch/push viram no-ops; o HEAD "avança" (E5)."""
    monkeypatch.setattr(gitops, "clone", lambda url, ws: None)
    monkeypatch.setattr(gitops, "configure_identity", lambda ws, **kw: None)
    monkeypatch.setattr(gitops, "head_sha", lambda ws: "base-sha-000")
    monkeypatch.setattr(gitops, "create_branch", lambda ws, br: None)
    monkeypatch.setattr(gitops, "has_new_commits", lambda ws, base: True)
    monkeypatch.setattr(gitops, "push", lambda ws, br, *, repo_url, token: None)


def promotion_gate(db: Any, flow: Any) -> Any:
    """O gate de promoção auto-aberto: a task humana ABERTA (sem decisão) em `done` (v2)."""
    rows = db.query(
        "SELECT VALUE id FROM $f<-belongs_to<-task WHERE state = 'done' AND decision IS NONE "
        "AND (->assigned_to->persona.catalog_name)[0] = 'humano';",
        {"f": flow},
    )
    return rows[0] if rows else None
