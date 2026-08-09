"""Guard de arquitetura: query tenant-scoped sem $tenant_id é violação (ADR-0053 §3).

O `ScopedStore` injeta `$tenant_id`/`$user_id` nos params, mas o WHERE continua
sendo responsabilidade de quem escreve a query. Sem este guard, o risco se
desloca de "esqueceu a checagem de membership" (grep-ável) para "esqueceu o
WHERE" (invisível) — que é pior. O guard converte isso em gate mecânico.

Regras de falha:
1. Query sobre tabela tenant-scoped sem `$tenant_id` → violação.
2. SQL não-literal (variável, f-string, concatenação) → violação, a menos que
   esteja na allowlist nominal com justificativa. Ausência de argumento SQL
   (call sem args, ou só kwargs não-SQL) também é não-literal — fail-closed,
   nunca ignorado.
3. `run_transaction(mod, [statements], ...)` é varrido: cada statement literal
   é verificado como query; statements não-literais seguem a regra 2. O
   caminho transacional é o único canal de escrita — cegue a ele é fail-open.
4. O guard vigia só os módulos já migrados — a baseline de não-migrados é
   explícita no teste e só encolhe.

Limitação registrada: a heurística de nome-de-tabela não enxerga acesso por
RecordID bind (`SELECT * FROM $r`) nem SQL montado cross-função. Nesses casos
o isolamento vem da chave determinística (tenant no hash do id) e da allowlist
nominal — não do predicado `WHERE tenant_id`. O guard não os substitui; garante
que o que tem nome de tabela visível não regride.
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

# Iteração determinística — a mensagem de violação não varia entre processos
# (hash de frozenset é randomizado; tuple ordenado não é).
_SORTED_TENANT_SCOPED_TABLES: tuple[str, ...] = tuple(sorted(TENANT_SCOPED_TABLES))

_QUERY_METHODS: frozenset[str] = frozenset({"query", "query_raw"})
_TRANSACTION_FUNCS: frozenset[str] = frozenset({"run_transaction"})
_SQL_KEYWORD = "sql"
_STATEMENTS_KEYWORD = "statements"


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


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Nomes de módulo atribuídos uma única vez a literal string/f-string → valor.

    Reatribuição (a qualquer coisa) ou import desqualifica o nome: o guard só
    confia em constantes imutáveis por inspeção estática. F-strings só entram
    se forem resolvíveis a partir de literais e outras constantes já conhecidas.
    """
    constants: dict[str, str] = {}
    disqualified: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in constants or target.id in disqualified:
                    disqualified.add(target.id)
                    constants.pop(target.id, None)
                    continue
                resolved = _resolve_sql(node.value, constants)
                if resolved is not None:
                    constants[target.id] = resolved
                else:
                    disqualified.add(target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[-1]
                disqualified.add(name)
                constants.pop(name, None)

    return constants


def _resolve_sql(arg: ast.AST, constants: dict[str, str]) -> str | None:
    """Resolve um node para um literal SQL string, ou None se não-literal.

    Aceita: string literal constante, `Name` que aponta para constante de
    módulo confiável, concatenação de strings já resolvíveis, ou f-string
    cujas partes são todas literais ou `Name` de constantes confiáveis. O
    resto (variável, call, expressão) é não-literal.
    """
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Name) and arg.id in constants:
        return constants[arg.id]
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        left = _resolve_sql(arg.left, constants)
        right = _resolve_sql(arg.right, constants)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(arg, ast.JoinedStr):
        parts: list[str] = []
        for value in arg.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = _resolve_sql(value.value, constants)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return "".join(parts)
    return None


def _references_tenant_scoped_table(sql: str) -> str | None:
    """Nome da primeira tabela tenant-scoped referenciada no SQL, ou None.

    Iteração em ordem alfabética — determinística para mensagens e asserts.
    """
    for table in _SORTED_TENANT_SCOPED_TABLES:
        if re.search(rf"\b{re.escape(table)}\b", sql, re.IGNORECASE):
            return table
    return None


def _is_allowed(module_name: str, func_name: str, allowlist: frozenset[AllowlistEntry]) -> bool:
    """True se a função está na allowlist nominal do módulo."""
    return any(e.module == module_name and e.function == func_name for e in allowlist)


def _missing_tenant_violation(lineno: int, table: str, method: str) -> Violation:
    return Violation(
        lineno=lineno,
        kind="missing_tenant_filter",
        message=f"query on tenant-scoped table '{table}' without $tenant_id in {method}()",
    )


def _non_literal_violation(lineno: int, func_name: str, method: str) -> Violation:
    return Violation(
        lineno=lineno,
        kind="non_literal_sql",
        message=(
            f"non-literal SQL in {func_name}() — {method}() requires "
            f"a string literal or an allowlist entry with justification"
        ),
    )


def _call_name(func: ast.AST) -> str | None:
    """Nome de uma call por `obj.m()` (attr) ou `m()` (name), ou None."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _keyword_arg(node: ast.Call, name: str) -> ast.AST | None:
    """Valor do keyword `name` na call, ou None."""
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _check_query_call(
    node: ast.Call,
    method: str,
    parents: dict[ast.AST, ast.AST],
    module_name: str,
    allowlist: frozenset[AllowlistEntry],
    constants: dict[str, str],
) -> list[Violation]:
    """Verifica uma call a `.query()`/`.query_raw()` — arg posicional ou keyword `sql`."""
    sql_arg = node.args[0] if node.args else _keyword_arg(node, _SQL_KEYWORD)
    func_name = _enclosing_function(node, parents)

    if sql_arg is None:
        # Sem argumento SQL: fail-closed (não ignorado).
        if _is_allowed(module_name, func_name, allowlist):
            return []
        return [_non_literal_violation(node.lineno, func_name, method)]

    sql = _resolve_sql(sql_arg, constants)
    if sql is None:
        if _is_allowed(module_name, func_name, allowlist):
            return []
        return [_non_literal_violation(node.lineno, func_name, method)]

    table = _references_tenant_scoped_table(sql)
    if table is not None and "$tenant_id" not in sql:
        return [_missing_tenant_violation(node.lineno, table, method)]
    return []


def _check_transaction_call(
    node: ast.Call,
    parents: dict[ast.AST, ast.AST],
    module_name: str,
    allowlist: frozenset[AllowlistEntry],
    constants: dict[str, str],
) -> list[Violation]:
    """Verifica cada statement literal de `run_transaction(mod, [stmts], ...)`.

    Lista de statements não-literal (variável, call) → non_literal_sql. Cada
    statement não-literal dentro da lista segue a regra de allowlist nominal.
    """
    stmts_arg = node.args[1] if len(node.args) > 1 else _keyword_arg(node, _STATEMENTS_KEYWORD)
    func_name = _enclosing_function(node, parents)

    if not isinstance(stmts_arg, ast.List):
        if _is_allowed(module_name, func_name, allowlist):
            return []
        return [_non_literal_violation(node.lineno, func_name, "run_transaction")]

    violations: list[Violation] = []
    for elt in stmts_arg.elts:
        sql = _resolve_sql(elt, constants)
        if sql is None:
            if not _is_allowed(module_name, func_name, allowlist):
                violations.append(_non_literal_violation(elt.lineno, func_name, "run_transaction"))
            continue
        table = _references_tenant_scoped_table(sql)
        if table is not None and "$tenant_id" not in sql:
            violations.append(_missing_tenant_violation(elt.lineno, table, "run_transaction"))
    return violations


def scan_module(
    source: str,
    filename: str,
    module_name: str,
    allowlist: frozenset[AllowlistEntry],
) -> list[Violation]:
    """Varre o código-fonte de um módulo e devolve as violações do guard.

    Procura chamadas a `.query()`/`.query_raw()` e `run_transaction(...)`,
    extrai o SQL e verifica:
    - Se é literal (ou constante de módulo confiável) e referencia tabela
      tenant-scoped → precisa de `$tenant_id`.
    - Se não é literal → precisa estar na allowlist com justificativa.
    """
    tree = ast.parse(source, filename=filename)
    parents = _build_parent_map(tree)
    constants = _module_string_constants(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name in _QUERY_METHODS:
            violations.extend(
                _check_query_call(node, name, parents, module_name, allowlist, constants)
            )
        elif name in _TRANSACTION_FUNCS:
            violations.extend(
                _check_transaction_call(node, parents, module_name, allowlist, constants)
            )

    return violations
