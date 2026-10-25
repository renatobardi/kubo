# ADR-0055 — Instância SurrealDB central: signin de DATABASE-level

> Status: **proposto** · Data: 2026-10-24
> Emenda ADR-0018 §I (credencial `kubo_rw`) apenas no que toca ao nível do usuário; preserva ADR-0053.

## Contexto

Os projetos Oute estão consolidando seus SurrealDB em uma instância central e isolada (repo `oute-surreal-db`: um namespace por projeto, databases `prd`/`test`). Hoje o Kubo se autentica com **três credenciais ROOT-level** (`root`, `kubo_ro` VIEWER, `kubo_rw` EDITOR) e faz `signin` só com usuário/senha (`kubo/store/client.py`). Numa instância compartilhada, um usuário ROOT enxerga **todos** os namespaces (inclusive o de outro projeto), o que anula o isolamento que justifica a instância central.

A instância central cria, por database, três usuários **de database**: `app_<db>` (OWNER), `rw_<db>` (EDITOR) e `ro_<db>` (VIEWER). Esses usuários só são encontrados pelo servidor se o signin trouxer `namespace` e `database`.

## Decisão

- `Config` ganha `auth_level` (`root` | `database`), lido de `SURREAL_AUTH_LEVEL`. **Default `root`**: dev, CI e o deploy próprio atual não mudam.
- Com `database`, `connect()` envia `namespace` e `database` no signin; o `use(ns, db)` continua.
- O usuário de escrita deixa de ser fixo: `rw_config()` lê `KUBO_RW_SURREAL_USER` (default `kubo_rw`) e herda `auth_level` da config base.
- Mapeamento no central: `root` → `app_<db>` (migrations e workers), `kubo_rw` → `rw_<db>`, `kubo_ro` → `ro_<db>`. O papel EDITOR continua suficiente para o caminho de escrita (ADR-0018 §I, `test_rw_user.py`): ele não gerencia usuários/acessos.
- Valor inválido de `SURREAL_AUTH_LEVEL` levanta `ConfigError` (não cai em `root` em silêncio).

## Consequências

- Positivo: a credencial do Kubo na instância central só alcança `kubo/<db>`; nenhum ROOT em app.
- Positivo: mudança pequena e retrocompatível (um campo, uma condicional); coberta por testes unitários e de integração contra SurrealDB real.
- Neutro: migrar o deploy para o central é configuração: o overlay `compose.central.yml` (banco local e backup atrás do profile `local-db`, `SURREAL_URL` por env) e as variáveis `SURREAL_AUTH_LEVEL=database`, usuários/senhas `app_/rw_/ro_<db>`. Procedimento e rollback em `runbook-deploy.md` §5b.
- Negativo/atenção: scripts de operação que fazem signin próprio (fora de `kubo/store/client.py`) precisam do mesmo ajuste (`namespace`/`database` no signin quando `SURREAL_AUTH_LEVEL=database`).
- Atenção: a instância central roda SurrealDB v3.3.0 e o Kubo está pinado em v3.1.5 ↔ SDK 2.0.0 (ADR-0005). A validação é o piloto kubo-test (dados já importados lá: 55 tabelas, 0 divergências); o bump do par SDK/servidor segue o ADR-0005.

## Alternativas rejeitadas

- **Manter usuários ROOT no central**: o Kubo leria os namespaces de outros projetos.
- **Usuários de namespace** (`ON NAMESPACE`): enxergariam `prd` e `test` juntos, quebrando a separação de ambientes.
- **Um SurrealDB por projeto (status quo)**: é justamente o que a instância central substitui.
