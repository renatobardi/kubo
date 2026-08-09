"""Guard de arquitetura: query tenant-scoped sem $tenant_id é violação (ADR-0053 §3).

O `ScopedStore` injeta `$tenant_id`/`$user_id` nos params, mas o WHERE continua
sendo responsabilidade de quem escreve a query. Sem este guard, o risco se
desloca de "esqueceu a checagem de membership" (grep-ável) para "esqueceu o
WHERE" (invisível) — que é pior. O guard converte isso em gate mecânico.

Regras de falha:
1. Query sobre tabela tenant-scoped sem `$tenant_id` → violação.
2. SQL não-literal (variável, f-string, concatenação) → violação, a menos que
   esteja na allowlist nominal com justificativa.
3. O guard vigia só os módulos já migrados — a baseline de não-migrados é
   explícita no teste e só encolhe.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Literal

# Tabelas com `tenant_id` no schema (migrations 0018–0042).
# Inclui tabelas de domínio, arestas e catálogos por tenant.
# NÃO inclui tabelas globais: `tenant`, `user`, `membership`, `user_profile`,
# `item`, `migration`, `invite`, `settings`.
TENANT_SCOPED_TABLES: frozenset[str] = frozenset(
    {
        # Domínio central (migration 0020)
        "flow",
        "task",
        "deliverable",
        "persona",
        "distilled",
        "entity",
        "chunk",
        "memory",
        "board_state",
        "git_repo",
        # Arestas (migration 0020)
        "belongs_to",
        "assigned_to",
        "produces",
        "consults",
        "derived_from",
        "mentions",
        "chunk_of",
        "produced_by",
        "relates_to",
        "in_state",
        "has_repo",
        # Coleta e execução (migration 0025)
        "source",
        "run",
        "dispatch",
        "destination",
        # Catálogos por tenant (migration 0018, ADR-0042)
        "catalog_persona",
        "catalog_integration",
        "catalog_flow_template",
        "catalog_changelog",
        # Tenancy scoped (migrations 0016–0017)
        "tenant_credential",
        "team_invite",
        # Estudo (migrations 0021–0031)
        "topic",
        "material",
        "material_chapter",
        "study_plan",
        "plan_entry",
        "lesson",
        "study_log",
        "study_chat",
        "material_section",
        # Resumo diário (migration 0042)
        "day_summary",
    }
)

_QUERY_METHODS: frozenset[str] = frozenset({"query", "query_raw"})


@dataclass(frozen=True)
class AllowlistEntry:
    """Exceção justificada para SQL não-literal num módulo migrado.

    Cada entrada é nominal: módulo + função + justificativa. A exceção é
    visível e contável — não é um bypass silencioso.
    """

    module: str
    function: str
    justification: str


@dataclass(frozen=True)
class Violation:
    """Violação encontrada pelo guard ao varrer um módulo."""

    lineno: int
    kind: Literal["missing_tenant_filter", "non_literal_sql"]
    message: str


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Mapa node → parent para encontrar a função que contém uma call."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """Nome da função que contém `node`, ou `<module>` se estiver no topo."""
    current: ast.AST | None = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current.name
        current = parents.get(current)
    return "<module>"


def _references_tenant_scoped_table(sql: str) -> str | None:
    """Nome da primeira tabela tenant-scoped referenciada no SQL, ou None."""
    for table in TENANT_SCOPED_TABLES:
        if re.search(rf"\b{re.escape(table)}\b", sql, re.IGNORECASE):
            return table
    return None


def _is_string_literal(node: ast.AST) -> bool:
    """True se o node é uma string literal constante (não f-string, não concat)."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def scan_module(
    source: str,
    filename: str,
    module_name: str,
    allowlist: frozenset[AllowlistEntry],
) -> list[Violation]:
    """Varre o código-fonte de um módulo e devolve as violações do guard.

    Procura chamadas a `.query()` e `.query_raw()`, extrai o SQL do primeiro
    argumento e verifica:
    - Se é literal e referencia tabela tenant-scoped → precisa de `$tenant_id`.
    - Se não é literal → precisa estar na allowlist com justificativa.
    """
    tree = ast.parse(source, filename=filename)
    parents = _build_parent_map(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in _QUERY_METHODS:
            continue

        sql_arg = node.args[0] if node.args else None
        if sql_arg is None:
            continue

        if _is_string_literal(sql_arg):
            assert isinstance(sql_arg, ast.Constant)
            assert isinstance(sql_arg.value, str)
            sql = sql_arg.value
            table = _references_tenant_scoped_table(sql)
            if table is not None and "$tenant_id" not in sql:
                violations.append(
                    Violation(
                        lineno=node.lineno,
                        kind="missing_tenant_filter",
                        message=(
                            f"query on tenant-scoped table '{table}' without $tenant_id "
                            f"in {func.attr}()"
                        ),
                    )
                )
        else:
            func_name = _enclosing_function(node, parents)
            allowed = any(e.module == module_name and e.function == func_name for e in allowlist)
            if not allowed:
                violations.append(
                    Violation(
                        lineno=node.lineno,
                        kind="non_literal_sql",
                        message=(
                            f"non-literal SQL in {func_name}() — {func.attr}() requires "
                            f"a string literal or an allowlist entry with justification"
                        ),
                    )
                )

    return violations
