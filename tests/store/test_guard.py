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
        "invites",
        "tenancy",
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
            function="_upsert_catalog_item",
            justification=(
                "run_transaction statements assembled from fixed templates "
                "(set_clause, changelog_stmt) with bind params"
            ),
        ),
        AllowlistEntry(
            module="catalog",
            function="_delete_catalog_item",
            justification=(
                "run_transaction assembles changelog_stmt from a fixed template with bind params"
            ),
        ),
        AllowlistEntry(
            module="catalog",
            function="seed_catalog",
            justification=(
                "run_transaction statements assembled from fixed UPSERT templates "
                "with bind params (coalesce pattern)"
            ),
        ),
        AllowlistEntry(
            module="knowledge",
            function="search",
            justification=(
                "k/ef are bounded integers computed by the store, not user input; "
                "the search vector goes via bind param"
            ),
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
    """SQL por concatenação com variável → violação non_literal_sql."""
    source = textwrap.dedent("""\
        def f(db, table):
            db.query("SELECT * FROM " + table)
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
# Scanner: keyword calls, constantes de módulo e run_transaction
# ---------------------------------------------------------------------------


def test_scan_detects_keyword_literal_missing_tenant() -> None:
    """Chamada por keyword com literal sem $tenant_id → violação (não é ignorada)."""
    source = textwrap.dedent("""\
        def f(db):
            db.query(sql="SELECT * FROM flow WHERE id = $id;")
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "missing_tenant_filter"


def test_scan_detects_keyword_non_literal() -> None:
    """Chamada por keyword com variável → violação non_literal_sql (fail-closed)."""
    source = textwrap.dedent("""\
        def f(db, sql):
            db.query(sql=sql)
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_detects_call_without_sql_argument() -> None:
    """Chamada sem argumento SQL → violação non_literal_sql (fail-closed)."""
    source = textwrap.dedent("""\
        def f(db):
            db.query()
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_resolves_module_string_constant() -> None:
    """Constante de módulo é resolvida e verificada como literal."""
    source = textwrap.dedent("""\
        _Q = "SELECT * FROM flow WHERE id = $id;"

        def f(db):
            db.query(_Q)
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "missing_tenant_filter"


def test_scan_resolved_constant_with_tenant_passes() -> None:
    """Constante de módulo com $tenant_id → sem violação."""
    source = textwrap.dedent("""\
        _Q = "SELECT * FROM flow WHERE tenant_id = $tenant_id;"

        def f(db):
            db.query(_Q)
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert violations == []


def test_scan_reassigned_constant_is_non_literal() -> None:
    """Nome reatribuído não é constante confiável → violação non_literal_sql."""
    source = textwrap.dedent("""\
        _Q = "SELECT * FROM flow WHERE tenant_id = $tenant_id;"
        _Q = build()

        def f(db):
            db.query(_Q)
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_imported_name_is_non_literal() -> None:
    """Nome importado (não definido no módulo) → violação non_literal_sql."""
    source = textwrap.dedent("""\
        from somewhere import _Q

        def f(db):
            db.query(_Q)
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_run_transaction_checks_literal_statements() -> None:
    """Statement literal de run_transaction sobre tabela tenant-scoped → violação."""
    source = textwrap.dedent("""\
        def f(db):
            run_transaction(db, ["DELETE FROM flow WHERE id = $id;"])
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "missing_tenant_filter"


def test_scan_run_transaction_passes_with_tenant() -> None:
    """Statement literal com $tenant_id → sem violação."""
    source = textwrap.dedent("""\
        def f(db):
            run_transaction(db, ["DELETE FROM flow WHERE tenant_id = $tenant_id;"])
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert violations == []


def test_scan_run_transaction_non_literal_statement() -> None:
    """Statement por variável em run_transaction → violação non_literal_sql."""
    source = textwrap.dedent("""\
        def f(db, stmt):
            run_transaction(db, ["LET $x = 1", stmt])
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_run_transaction_non_list_statements() -> None:
    """Statements fora de lista literal (variável) → violação non_literal_sql."""
    source = textwrap.dedent("""\
        def f(db, statements):
            run_transaction(db, statements)
    """)
    violations = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(violations) == 1
    assert violations[0].kind == "non_literal_sql"


def test_scan_run_transaction_allowlisted() -> None:
    """Statement não-literal allowlisted com justificativa → sem violação."""
    source = textwrap.dedent("""\
        def build(db, stmt):
            run_transaction(db, [stmt])
    """)
    allowlist = frozenset(
        {
            AllowlistEntry(
                module="synthetic",
                function="build",
                justification="statements assembled from fixed templates",
            ),
        }
    )
    violations = scan_module(source, "synthetic.py", "synthetic", allowlist)
    assert violations == []


def test_violation_message_deterministic_for_multi_table_sql() -> None:
    """SQL com 2+ tabelas tenant-scoped nomeia sempre a mesma (ordem estável)."""
    source = textwrap.dedent("""\
        def f(db):
            db.query("SELECT * FROM task JOIN flow ON task.flow = flow.id;")
    """)
    first = scan_module(source, "synthetic.py", "synthetic", frozenset())
    second = scan_module(source, "synthetic.py", "synthetic", frozenset())
    assert len(first) == 1
    assert first[0].message == second[0].message
    assert "flow" in first[0].message  # ordem alfabética: flow < task


# ---------------------------------------------------------------------------
# Guard real: varredura dos módulos migrados
# ---------------------------------------------------------------------------


STORE_DIR = Path(__file__).resolve().parent.parent.parent / "kubo" / "store"


def _store_modules() -> set[str]:
    """Lista módulos .py em kubo/store/ (sem __init__, __main__, __pycache__)."""
    return {p.stem for p in STORE_DIR.glob("*.py") if not p.name.startswith("__")}


# Módulos já migrados para ScopedStore — vigiados pelo guard.
# Só cresce — quando um módulo migra, sai da baseline e entra aqui.
MIGRATED: frozenset[str] = frozenset(
    {
        "catalog",
        "destinations",
        "flows",
        "knowledge",
        "seed",
        "seed_extra_rss",
        "settings",
        "study",
        "team_invites",
        "tenant_credentials",
    }
)


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


# Tetos de tamanho: a baseline só encolhe e a allowlist não vira bypass geral.
# Ao migrar um módulo, BAIXE o teto da baseline junto.
_BASELINE_MAX_SIZE = 4
_ALLOWLIST_MAX_SIZE = 6


def test_baseline_size_does_not_grow() -> None:
    """A baseline não pode crescer — migrar de volta exige baixar o teto aqui."""
    assert len(BASELINE_NOT_MIGRATED) <= _BASELINE_MAX_SIZE


def test_allowlist_size_ceiling() -> None:
    """Allowlist com teto — exceções são nominais e contáveis, não um bypass geral."""
    assert len(_ALLOWLIST) <= _ALLOWLIST_MAX_SIZE


def test_baseline_and_migrated_do_not_overlap() -> None:
    """Um módulo não pode estar na baseline e sob o guard ao mesmo tempo."""
    overlap = BASELINE_NOT_MIGRATED & MIGRATED
    assert not overlap, f"Modules in both baseline and migrated: {overlap}"


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
