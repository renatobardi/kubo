# ADR-0007 — Mecânica de migrations do SurrealDB

> Status: **aceito** · Data: 2026-07-05

## Contexto

O schema do SurrealDB (tabelas, arestas, índices — spec §2.3) precisa evoluir de forma versionada e reproduzível entre dev, CI e a produção OCI. O M2 entrega o **esqueleto** do runner que o M3 (schema de conhecimento) consome. A premissa de fadiga de complexidade veta um framework de migrations pesado.

## Decisão

Runner mínimo (`kubo/store/migrations/`), ~40 linhas, por TDD:

1. **Arquivos `.surql` numerados** vivem no pacote `kubo/store/migrations/`, aplicados **em ordem de nome** (não de criação). Convenção: prefixo numérico (`0001_…`, `0002_…`).
2. **Tabela `migration`** registra o que já rodou (nome + `applied_at`). Antes de aplicar, o runner lê o conjunto já aplicado.
3. **Aplicação sequencial e idempotente no nível do runner:** cada arquivo roda uma vez; reexecutar sem migration nova é **no-op**.
4. **Aplicar + registrar é atômico por migration:** cada `.surql` e o `CREATE migration` correspondente rodam numa única transação SurrealQL (`BEGIN; … ; COMMIT;`) — não fica registro órfão nem migration aplicada sem registro (fecha achado da revisão de segurança, verificado no pin v3.1.5). Migrations anteriores já commitadas não são revertidas por uma falha posterior.
5. **Sem down-migrations.** Não há rollback automático de schema.
6. Todo acesso passa pela store (invariante 2); o runner recebe a conexão, não a abre por conta própria.

## Consequências

Simples de ler e auditar; o histórico de schema é o conjunto de `.surql` versionados + a tabela `migration`. Reexecução segura torna o runner idempotente do ponto de vista de quem chama.

**Restrições que a atomicidade impõe aos `.surql`:** o runner envolve cada arquivo em `BEGIN;…;COMMIT;`, então (a) um `.surql` **não** pode ter controle de transação próprio, e (b) toda DDL usada deve poder rodar dentro de transação (verificado para `DEFINE TABLE`/`CREATE` no pin; validar novas formas de DDL ao adotá-las). Convenção mantida por robustez: **DDL idempotente** (`DEFINE … OVERWRITE` / `IF NOT EXISTS`).

## Alternativas rejeitadas

(a) **Down-migrations / rollback automático de schema** — rejeitada: dobra a superfície (todo `up` precisa de um `down` correto e testado) para um ganho que, num projeto solo com backup diário (D2), é melhor servido por **restaurar backup**. Rollback é restaurar snapshot, não desfazer DDL.

(b) Framework de migrations de terceiros (Alembic-like) — rejeitada: dependência nova e conceitos (autogenerate, branching) desproporcionais ao SurrealQL declarativo e ao tamanho do projeto.

(c) Aplicar todo o schema num único arquivo idempotente a cada boot — rejeitada: perde o registro de *quando* cada mudança entrou e não distingue migration de dado de migration de schema.
