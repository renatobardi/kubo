# ADR-0053 — ScopedStore: escopo de tenant na sessão de store, não na assinatura

> Status: **proposto** · Data: 2026-08-08 · Cumpre ADR-0039 §II (aceito); emenda a forma de enforcement, não a regra.
> **Depende do ADR-0044 (ainda `proposto`)**: esta fatia fecha a dívida nomeada no §2 dele. O 0044 é aceito antes — ou no mesmo lote — do merge desta fatia.

## Contexto

O ADR-0039 (aceito) exige: "toda operação tenant-scoped no `kubo/store/` exige uma linha de `membership` válida para `(user_id, tenant_id)` antes de executar". A evidência (main @ a1152f3) mostra que o enforcement atual é por disciplina função-a-função:

- 166 de 209 funções públicas recebem `tenant_id` como parâmetro manual.
- 22 não chamam `assert_membership`; 9 são a maquinaria em `tenancy.py` (legítimo) → **13 lacunas reais**.
- 7 das 13 estão em `destinations.py`, arquivo com zero `assert_membership`; `edit_destination` e `delete_destination` nem recebem `tenant`.
- 0 cláusulas `PERMISSIONS` em 41 migrations — isolamento é 100% Python, aplicado uma função por vez.
- `assert_membership_if_given` silencia quando ambos os ids são None — é a porta de entrada para lacunas.

O problema não é a regra, é o mecanismo de enforcement: 166 assinaturas reenunciam `tenant_id`/`user_id`, e o esquecimento da checagem é um bug por função, não um bug estrutural.

## Decisão

O escopo de tenant deixa de ser parâmetro de assinatura e passa a viver na **sessão de store** — um `ScopedStore` que envolve a conexão SurrealDB, segura `(tenant_id, user_id, superadmin)`, checa `membership` uma vez na criação, e injeta `tenant_id`/`user_id` nos params de todo `query()` que emite.

### Mecanismo

1. **`ScopedStore`** — wrapper sobre `db`. Override de `query(sql, params)` **e de `query_raw(sql, params)`**: injeta `self.tenant_id` e `self.user_id` no dict de params antes de delegar. O caller não passa esses params; a query os referencia como `$tenant_id`/`$user_id`.
2. **Caminho transacional** — `query_raw` é o único canal de escrita transacional (`kubo/store/transaction.py:35`, **47 call sites** de `run_transaction`). Sobrescrever só `query()` deixaria toda a escrita fora da injeção, silenciosamente. Portanto `run_transaction` muda a assinatura para `run_transaction(session: ScopedStore, ...)` e delega ao `query_raw` do wrapper.
3. **Guard de arquitetura** (item de decisão, não *nice to have*) — teste que varre os literais SQL dos módulos tenant-scoped e **falha** quando uma query sobre tabela tenant-scoped não referencia `$tenant_id`, e quando uma função pública de `kubo/store/` aceita `Any`. Regex sobre literal é aproximada e falha para o lado seguro; é o que converte "esqueceu o WHERE" de disciplina de review em gate mecânico. Sem este guard o `ScopedStore` desloca o problema em vez de fechá-lo (ver Trade-offs).
4. **Factories distintas** `scoped(db, tenant_id, user_id)` e `scoped_superadmin(db, user_id)` — não um kwarg booleano. Flag booleana desliga tenancy para uma sessão inteira e pode ser alimentada por valor derivado de input de request; duas factories são grep-áveis e a escolha é sítio de código, não dado. A rota administrativa pede explicitamente via `get_scoped_store_admin`; não é automático pela allowlist.
5. **Assinaturas** — 166 funções mudam de `db: Any, *, tenant_id, user_id, ...` para `session: ScopedStore, *, ...`. `tenant_id` e `user_id` somem da assinatura. Funções que precisam de `user_id` para domínio (auditoria, `created_by`) leem `session.user_id`.
6. **Tipo** — `session: ScopedStore` é tipado. O caminho pool ganha tipo próprio `PoolReader` (wrapper sem tenant), e `db: Any` deixa de existir em API pública da store — sobra só dentro de `tenancy.py`. Enquanto `Any` for o shape do bypass, o tipo não carrega garantia nenhuma.
7. **Pool reads** — `item_index` e `items_by_ids` (tabela `item`, pool sem `tenant_id` no schema) recebem `PoolReader`, não `ScopedStore`. A separação na interface reflete a regra dos dois caminhos do ADR-0039 §III.
8. **`recent_runs` e `list_sources`** — passam a filtrar por tenant. `source` e `run` têm `tenant_id` obrigatório desde a migration 0025; os docstrings "tabela global" estavam desatualizados. **Isto é mudança de comportamento em produção, não refactor** — resultado de query muda — e vai em PR próprio, com teste.
9. **`destination`** — migration nova faz `tenant_id TYPE record<tenant> ASSERT $value != NONE` + backfill para `tenant:breakglass`. As 7 funções de `destinations.py` ganham escopo. Fecha a dívida nomeada no ADR-0044 §2. Pré-condição operacional: **contar as linhas de `destination` no PRD antes de aplicar o `ASSERT`** — amarrar destinos de tenants reais ao `breakglass` é entrega no lugar errado, não sujeira cosmética.
10. **`assert_membership_if_given`** — deletada. A transição para tenancy obrigatório (KUBO-117) que ela servia é completada por esta fatia.
11. **`assert_membership_or_superadmin` sobrevive.** Morre apenas o uso *dentro* das funções de store (7 sítios em `knowledge.py`/`flows.py`), absorvido pela factory. Os callers de API (`kubo/api/session.py:77`, `kubo/api/routes/auth.py:508`) não têm relação com escopo de store e permanecem.
12. **Vida da sessão == vida do request.** Membership é checada uma vez na criação; uma revogação só passa a valer no próximo `ScopedStore`. Isso é aceitável enquanto a sessão nasce e morre dentro de um request (ou de um tick do scheduler). É **proibido** guardar `ScopedStore` em app state ou construí-lo no startup do processo — um por tick, nunca um por processo.
13. **Dependencies FastAPI** — `get_scoped_store` (read, sobre `connect()`) e `get_scoped_store_rw` (write, sobre `connect_rw()`). Least privilege preservado. Membership check roda na mesma conexão da operação.
14. **Scheduler/workers** — scheduler constrói `scoped(db, *resolve_scheduler_tenant_and_user(db))` por tick e passa o `ScopedStore` para o worker. Worker recebe pronto; não resolve tenant.
15. **Seed e scripts** — sempre usam `ScopedStore` (ou `scoped_superadmin` para ops administrativos). Sem carve out por contexto.

### Piso

Só Python. Sem `PERMISSIONS` no SurrealDB — a conexão é root/`kubo_rw`, que bypassa RLS nativo. PERMISSIONS com conexão root é teatro. Fica como hardening futuro dependente de trocar o modelo de auth (record-user), fora desta fatia.

### Estratégia de entrega

**Incremental, com o valor de segurança front-loaded.** O big bang (166 assinaturas + callers + migration + testes num PR) é review-teatro: neste repo os achados que importaram (Major do CodeRabbit no #112, paginação no #61, security-reviewer na 0018b) vieram de review que conseguia ler o diff. E o argumento do "estado intermediário misto" não se sustenta contra o item 6: os dois tipos são distinguíveis por pyright — **estado misto tipado é migração legível, não ambiguidade**.

| PR | Conteúdo | Por quê |
|---|---|---|
| **PR0** | Migration de `destination` (option → `record<tenant>` ASSERT) + backfill | Risco de dados tem rollback diferente de risco de interface; um backfill ruim não pode obrigar a reverter um refactor sadio. Vale sozinho, fecha ADR-0044 §2 sem depender do `ScopedStore`. |
| **PR1** | `ScopedStore` + `PoolReader` + factories + override (`query` e `query_raw`) + `run_transaction` + guard de arquitetura + migração de `destinations.py` e seus callers | Fecha **7 das 13 lacunas** — o grosso do risco — em diff revisável. Os callers de `destinations.py` são concentrados (`api/routes/destinations.py`, `routes/settings.py`, `store/settings.py`, `scheduler`, `seed`), então o corte por módulo existe de fato. |
| **PR2..N** | Um módulo de store por PR (`knowledge`, `flows`, `study`, `catalog`, …) | Mecânico, com o guard de arquitetura já vigilante sobre o que entra. |
| **PR próprio** | `recent_runs` / `list_sources` passando a filtrar por tenant | Mudança de comportamento em produção; invisível dentro de um PR de 166 assinaturas. |
| **PR final** | Deleta `assert_membership_if_given`; guard passa a proibir `Any` em API pública da store | O fecho só é declarável quando não sobra caminho antigo. |

Critério de reversão da estratégia: se `git grep` dos callers mostrar que um módulo não é destacável sem arrastar outro junto, aquele par vai num PR só — não se volta ao big bang por isso.

## Consequências

- **Positivo:** o esquecimento da checagem de membership vira inexprimível na assinatura — não há parâmetro para omitir. As 13 lacunas reais fecham estruturalmente, não por disciplina.
- **Positivo:** interface perde 2 params × 166 assinaturas. Membership checada 1 vez por sessão, não 166.
- **Positivo:** `destinations.py` deixa de ser a exceção com zero checagem. `destination` ganha `tenant_id` obrigatório no schema.
- **Positivo:** pyright distingue `ScopedStore` (tenant-scoped) de `PoolReader` (pool) — o tipo carrega a garantia porque `Any` deixa de ser o shape do bypass.
- **Trade-off (o central):** o WHERE de tenant na query continua responsabilidade do autor — o `ScopedStore` injeta os params, não reescreve SQL. Sozinho, o wrapper **desloca** o risco de "esqueceu a checagem de membership" (13 lacunas grep-áveis por `assert_membership`) para "esqueceu o WHERE" (N lacunas invisíveis, só achadas lendo SQL) — que é pior, porque perde a auditabilidade. O que torna a troca um ganho líquido é o **guard de arquitetura** (mecanismo §3): sem ele, este ADR não se justifica. RLS nativo mitigaria de vez; adiado por exigir troca do modelo de conexão.
- **Trade-off:** entrega em série de PRs prolonga o estado misto por algumas semanas. Mitigado por (a) o estado misto ser tipado e (b) as 7 lacunas de maior risco fecharem já no PR1.
- **Trade-off:** membership passa de revalidada por chamada para checada 1× por sessão — revogação só vale no próximo `ScopedStore`. Contido pela regra "vida da sessão == vida do request" (mecanismo §12); vira exposição real se alguém guardar a sessão em app state.
- **Neutro:** o backfill de PRD (KUBO-146, ADR-0044 §5) segue aberto. Esta fatia usa o padrão `breakglass` da 0025 para `destination`, com a contagem prévia no PRD como pré-condição (mecanismo §9).

## Alternativas rejeitadas

- **Variável de sessão SurrealDB (`LET $tenant_id`)** — rejeitada não por incerteza do SDK, mas porque **falha aberta**: com conexão por request e pool, se a variável não persistir no socket a query roda *sem filtro* em vez de estourar. Estado invisível cujo modo de falha é vazamento silencioso de dados entre tenants.
- **Reescrita de query automática** — o wrapper interceptaria SQL e injetaria `WHERE tenant_id = ...`. Parsing de SurrealQL é não-trivial (graph traversal, nested queries); fragilidade alta, ganho marginal sobre a injeção de params.
- **`ScopedStore` com `__getattr__` delegando tudo** — mínimo de mudança, mas o tipo continua `Any`; perde o ganho de type-safety. Pyright não distingue escopado de não-escopado.
- **Big bang num PR único** — *rejeitada* (era a decisão anterior deste ADR). 166 assinaturas + callers + migration + testes num diff não recebem review de verdade, e amarram risco de dados (migration) a risco de interface (refactor) num mesmo rollback. O medo do "estado intermediário misto" se dissolve com o item 6: o misto é distinguível por pyright.
- **Incremental "puro", sem PR1 de segurança** — migrar módulo a módulo em ordem arbitrária deixaria `destinations.py` (7 das 13 lacunas, zero `assert_membership`) para o fim. A ordem importa mais que o número de PRs.
- **`PERMISSIONS` no SurrealDB desde já** — conexão root/`kubo_rw` bypassa RLS nativo; seria código morto no schema com falsa impressão de dupla camada.
