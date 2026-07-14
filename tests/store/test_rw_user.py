"""Credencial de ESCRITA da UI: kubo_rw (ROOT-level EDITOR) — ADR-0018 §I.

Duas provas: (1) `rw_config` é fail-fast por env (sem `KUBO_RW_SURREAL_PASS` → ConfigError, o
que os handlers traduzem em 503) e herda url/ns/db da config base trocando só user/senha
(unit, sem banco); (2) um EDITOR ROOT-level executa TODAS as escritas do caminho de gate —
instanciar, criar task, transicionar, decidir — provando que `ROLES EDITOR` basta (não precisa
OWNER, que gerenciaria usuários e escalaria privilégio). Criação do usuário aqui é fixture
efêmera; em produção é passo one-time do runbook, NUNCA migration (senha em .surql fura o §8).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from kubo.errors import ConfigError
from kubo.runtime.flow_templates import load_flow_template
from kubo.runtime.personas import load_personas
from kubo.store import client, migrations
from kubo.store.flows import create_task, decide_gate, instantiate_flow, transition_task

_RW_DB = "test_rw_user"
_RW_USER = "kubo_rw"
_RW_PASS = "editor-ephemeral-test-pw"  # pragma: allowlist secret  # container efêmero, descartado
_CATALOG = Path(__file__).parents[2] / "catalogs"
_PERSONAS = load_personas(_CATALOG / "personas")


def test_rw_config_requires_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-fast: sem `KUBO_RW_SURREAL_PASS` → ConfigError (os handlers dão 503; a UI de
    leitura segue viva). A senha nunca tem default (invariante 8)."""
    monkeypatch.delenv("KUBO_RW_SURREAL_PASS", raising=False)
    with pytest.raises(ConfigError, match="KUBO_RW_SURREAL_PASS"):
        client.rw_config()


def test_rw_config_overrides_only_user_and_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """rw_config herda url/ns/db da config base (MESMO endpoint do kubo_ro) e troca só
    user→kubo_rw e password→env."""
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", "rw-secret")  # pragma: allowlist secret
    base = client.config()
    cfg = client.rw_config()
    assert cfg.user == "kubo_rw"
    assert cfg.password == "rw-secret"  # pragma: allowlist secret
    assert cfg.url == base.url
    assert cfg.namespace == base.namespace
    assert cfg.database == base.database


@pytest.fixture
def rw_env() -> Iterator[tuple[Any, Any]]:
    """Sobe um db efêmero com schema, define um usuário ROOT-level EDITOR e entrega (conexão
    root, conexão do editor). Remove usuário e db no teardown — nada vaza para o servidor."""
    root_cfg = replace(client.config(), database=_RW_DB)
    rw_cfg = replace(root_cfg, user=_RW_USER, password=_RW_PASS)
    with client.connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_RW_DB};")
        root.use(root_cfg.namespace, root_cfg.database)
        migrations.apply_migrations(root)
        # ROOT-level EDITOR: mesma forma de signin do root (sem ns/db) — Path A.
        root.query(f"DEFINE USER {_RW_USER} ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")
        try:
            with client.connect(rw_cfg) as rw:
                yield root, rw
        finally:
            root.query(f"REMOVE USER IF EXISTS {_RW_USER} ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_RW_DB};")


@pytest.mark.integration
def test_editor_role_suffices_for_gate_writes(rw_env: tuple[Any, Any]) -> None:
    """O EDITOR executa TODA a escrita do gate pela conexão rw — CREATE, RELATE, UPDATE e a
    transação de `decide_gate`. Read-back COMO ROOT prova que as escritas caíram (a trilha do
    footgun do no-op silencioso no nível da credencial): se EDITOR não bastasse, nada gravaria."""
    root, rw = rw_env
    template = load_flow_template(_CATALOG / "flow_templates" / "analysis-review.yaml")

    inst = instantiate_flow(rw, template=template, personas=_PERSONAS, question="q?")
    analyst = create_task(rw, flow=inst.flow, persona=inst.personas["analista"], state="created")
    transition_task(rw, analyst, from_state="created", to_state="analyzing")
    transition_task(rw, analyst, from_state="analyzing", to_state="awaiting_review")
    gate = create_task(rw, flow=inst.flow, persona=inst.personas["humano"], state="awaiting_review")
    decide_gate(rw, analyst_task=analyst, gate_task=gate, to_state="delivered", decision="approved")

    # lê COMO ROOT: as escritas do EDITOR persistiram de verdade
    assert root.query("SELECT VALUE state FROM $t;", {"t": analyst})[0] == "delivered"
    decided = root.query("SELECT state, decision FROM $t;", {"t": gate})[0]
    assert decided["state"] == "delivered"
    assert decided["decision"] == "approved"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
