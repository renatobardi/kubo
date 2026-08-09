"""Guard de arquitetura: query tenant-scoped sem $tenant_id falha o teste (ADR-0053 §3).

O guard varre os literais SQL dos módulos migrados e falha quando:
1. Uma query sobre tabela tenant-scoped não referencia `$tenant_id`.
2. SQL não-literal (montado dinamicamente ou vindo de variável) é usado sem
   estar na allowlist nominal com justificativa.

A baseline de módulos não migrados é dado explícito do teste — só encolhe.
PR2 (KUBO-212): catalog.py migra; a baseline encolhe de um módulo.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from kubo.store.guard import (
    TENANT_SCOPED_TABLES,
    AllowlistEntry,
    Violation,
    scan_module,
)

# Módulos da store que ainda NÃO foram migrados para ScopedStore.
# Só encolhe — quando um módulo migra, sai daqui e entra no escopo do guard.
BASELINE_NOT_MIGRATED: frozenset[str] = frozenset(
    {
        "client",
        "destinations",
        "flows",
        "invites",
        "knowledge",
        "seed",
        "seed_extra_rss",
        "settings",
        "study",
        "team_invites",
        "tenancy",
        "tenant_credentials",
        "transaction",
    }
)

# Allowlist de SQL não-literal justificado para módulos já migrados.
# Cada entrada é nominal: módulo + função + justificativa.
_ALLOWLIST: frozenset[AllowlistEntry] = frozenset(
    {
        AllowlistEntry(
            module="catalog",
            function="_list_catalog_items",
            justification="table name is one of three fixed catalog tables, not user input",
        ),
        AllowlistEntry(
            module="catalog",
            function="_get_catalog_item",
            justification="SQL is a module-level constant (_SELECT_BY_ID), not dynamic",
        ),
        AllowlistEntry(
            module="catalog",
            function="_upsert_catalog_item",
            justification="SQL is a module-level constant (_SELECT_BY_ID), not dynamic",
        ),
    }
)

# Módulos que não têm queries tenant-scoped (infraestrutura, não migráveis).
# `scoped` é o wrapper; `guard` é o scanner do próprio guard.
_EXEMPT: frozenset[str] = frozenset({"scoped", "guard"})


# ---------------------------------------------------------------------------
# Scanner: testes unitários com código sintético
# ---------------------------------------------------------------------------


def test_scan_detects_missing_tenant_filter() -> None:
    """Query sobre tabela tenant-scoped sem $tenant_id → violação."""
    source = textwrap.dedent("""\
        def f(db):
            db.query("SELECT * FROM flow WHERE id = $id;", {"id": 1})
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "missing_tenant_filter"
    assert "flow" in violations[0].message


def test_scan_passes_with_tenant_filter() -> None:
    """Query sobre tabela tenant-scoped com $tenant_id → sem violação."""
    source = textwrap.dedent("""\
        def f(db):
            db.query("SELECT * FROM flow WHERE tenant_id = $tenant_id AND id = $id;", {"id": 1})
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert violations == []


def test_scan_passes_non_tenant_table() -> None:
    """Query sobre tabela global (sem tenant_id) → sem violação."""
    source = textwrap.dedent("""\
        def f(db):
            db.query("SELECT * FROM tenant WHERE id = $id;", {"id": 1})
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert violations == []


def test_scan_detects_non_literal_sql() -> None:
    """SQL montado dinamicamente (variável) → violação non_literal_sql."""
    source = textwrap.dedent("""\
        def f(db):
            sql = "SELECT * FROM flow;"
            db.query(sql)
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_detects_fstring_sql() -> None:
    """SQL em f-string → violação non_literal_sql (não é literal constante)."""
    source = textwrap.dedent("""\
        def f(db, table):
            db.query(f"SELECT * FROM {table};")
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_detects_concatenated_sql() -> None:
    """SQL por concatenação → violação non_literal_sql."""
    source = textwrap.dedent("""\
        def f(db):
            db.query("SELECT * FROM " + "flow;")
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_allowlist_exempts_non_literal() -> None:
    """SQL não-literal na allowlist com justificativa → sem violação."""
    source = textwrap.dedent("""\
        def build_query(db):
            sql = "SELECT * FROM flow;"
            db.query(sql)
    """)
    allowlist = frozenset(
        {
            AllowlistEntry(
                module="synthetic",
                function="build_query",
                justification="dynamic table name",
            ),
        }
    )
    violations = scan_module(source, "synthetic.py", "synthetic", allowlist)
    assert violations == []


def test_scan_query_raw_also_scanned() -> None:
    """query_raw é coberto pelo guard, não só query."""
    source = textwrap.dedent("""\
        def f(db):
            db.query_raw("SELECT * FROM dispatch WHERE id = $id;", {"id": 1})
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "missing_tenant_filter"


def test_scan_multiple_violations() -> None:
    """Múltiplas violações no mesmo módulo são todas reportadas."""
    source = textwrap.dedent("""\
        def f(db):
            db.query("SELECT * FROM flow WHERE id = $id;", {"id": 1})
            db.query("SELECT * FROM task WHERE id = $id;", {"id": 2})
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 2


def test_scan_no_query_calls() -> None:
    """Módulo sem query/query_raw → sem violações."""
    source = textwrap.dedent("""\
        def f(x):
            return x + 1
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert violations == []


def test_tenant_scoped_tables_includes_core() -> None:
    """A lista de tabelas tenant-scoped inclui as centrais do produto."""
    for table in (
        "flow",
        "task",
        "source",
        "run",
        "dispatch",
        "destination",
        "catalog_persona",
        "catalog_integration",
        "catalog_flow_template",
    ):
        assert table in TENANT_SCOPED_TABLES, f"{table} should be tenant-scoped"


def test_tenant_scoped_tables_excludes_global() -> None:
    """A lista NÃO inclui tabelas globais (sem tenant_id no schema)."""
    for table in ("tenant", "user", "membership", "user_profile", "item", "migration"):
        assert table not in TENANT_SCOPED_TABLES, f"{table} should NOT be tenant-scoped"


# ---------------------------------------------------------------------------
# Guard real: varredura dos módulos migrados
# ---------------------------------------------------------------------------


STORE_DIR = Path(__file__).resolve().parent.parent.parent / "kubo" / "store"


def _store_modules() -> set[str]:
    """Lista módulos .py em kubo/store/ (sem __init__, __main__, __pycache__)."""
    return {p.stem for p in STORE_DIR.glob("*.py") if not p.name.startswith("__")}


# Módulos já migrados para ScopedStore — vigiados pelo guard.
# Só cresce — quando um módulo migra, sai da baseline e entra aqui.
MIGRATED: frozenset[str] = frozenset({"catalog"})


def test_all_store_modules_classified() -> None:
    """Todo módulo da store está classificado: baseline, exempt, ou migrado."""
    all_modules = _store_modules()
    unclassified = all_modules - BASELINE_NOT_MIGRATED - _EXEMPT - MIGRATED
    assert not unclassified, f"Store modules not classified: {unclassified}"


def test_baseline_only_shrinks() -> None:
    """A baseline não inclui módulos inexistentes — só módulos reais."""
    all_modules = _store_modules()
    phantom = BASELINE_NOT_MIGRATED - all_modules
    assert not phantom, f"Baseline references non-existent modules: {phantom}"


def test_guard_migrated_modules_pass() -> None:
    """Módulos migrados passam o guard sem violações.

    No PR2 (KUBO-212), catalog.py migra — a baseline encolhe de um módulo.
    O guard vigia catalog.py com a allowlist de SQL não-literal justificado.
    """
    all_violations: list[tuple[str, Violation]] = []
    for mod_name in sorted(MIGRATED):
        path = STORE_DIR / f"{mod_name}.py"
        source = path.read_text(encoding="utf-8")
        violations = scan_module(source, str(path), mod_name, _ALLOWLIST)
        for v in violations:
            all_violations.append((mod_name, v))

    assert not all_violations, "Guard violations in migrated modules:\n" + "\n".join(
        f"  {mod}:{v.lineno} [{v.kind}] {v.message}" for mod, v in all_violations
    )
