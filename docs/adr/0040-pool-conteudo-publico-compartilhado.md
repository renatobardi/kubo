# ADR-0040 — Pool de conteúdo público compartilhado entre tenants (mecanismo de dedup)

> Status: **aceito** · Data: 2026-07-25 · **Emenda o ADR-0039 §III** (a regra dos dois caminhos permanece; só a atribuição de tabelas muda — `item` sai da lista tenant-scoped e vira pool inteiro, `source` é redefinido).

## Contexto

Resolve o ticket [KUBO-107](https://oute.atlassian.net/browse/KUBO-107) do mapa wayfinder [KUBO-104](https://oute.atlassian.net/browse/KUBO-104), consumindo os achados factuais de [KUBO-106](https://oute.atlassian.net/browse/KUBO-106) e a regra dos dois caminhos travada no ADR-0039 (KUBO-105).

Achado central da research (KUBO-106): não existe hash de conteúdo em lugar nenhum hoje. `source` já é lookup-first por `(kind, canonical)` (`upsert_source`, endurecido no incidente #105/#106); `item` é `(source, external_id)`, escopado a uma única source, sem qualquer identidade cross-source. `since=created_at` existe só para `github-repo` (sem backfill) — RSS não tem equivalente, depende só da idempotência do `upsert_item`.

## Decisão

### I. Dedup por fonte exata, não por conteúdo

O pool deduplica pela **identidade da fonte pública** (`kind` + `canonical`), a mesma chave que `upsert_source` já usa — não por hash de conteúdo. Dois tenants que cadastram a mesma URL/feed/repo compartilham a coleta física; o mesmo artigo publicado em duas fontes diferentes continua sendo coletado 2x (sem dedup semântico). Escopo deliberadamente menor que dedup por conteúdo, que exigiria infra nova (hashing, comparação) inexistente hoje.

### II. Split de tabelas — emenda ao ADR-0039 §III

A regra dos dois caminhos do ADR-0039 (toda tabela é 100% tenant-scoped OU 100% pool, nunca `tenant_id` anulável) **não muda**. O que muda é qual tabela cai em qual caminho:

- **`pool_source`** (nova, substitui a identidade de coleta que hoje vive em `source`): campos `kind`, `canonical`, `created_at`. Herda o índice `UNIQUE(kind, canonical)` que `source` tem hoje. **Sem `tenant_id`, nunca.**
- **`pool_item`** (nova, substitui `item` integralmente): campos `external_id`, `url`, `title`, `content`, `metadata`, `collected_at`, referenciando `pool_source`. **Sem `tenant_id`, nunca.** `item` deixa de existir como tabela tenant-scoped — a lista do ADR-0039 §III que a incluía fica emendada aqui.
- **`source`** (nome mantido, **significado redefinido**): vira a **Cadastro** do tenant — `tenant_id` obrigatório, referencia `pool_source` via edge (`source -[collects]-> pool_source`), carrega estado por-tenant: tags, título, o modelo de 3 estados (pausar/arquivar/apagar, já decidido na sessão 107 do épico de Cadastro). Vários `source` de tenants diferentes podem apontar para o mesmo `pool_source`.
- **`distilled`** continua tenant-scoped (sem mudança de escopo) — o edge `derived_from` passa a apontar para `pool_item` em vez de um `item` tenant-owned.

### III. Invariante: só conteúdo público entra no pool

O pool existe **só para fontes públicas** (RSS, GitHub público, futuro webscraping de página pública) — é o que torna o compartilhamento entre tenants seguro por design. Qualquer `kind` futuro que exija credencial por-tenant (repo privado, API autenticada, upload próprio do tenant) **nunca** pode entrar em `pool_source`/`pool_item` — colocaria dado de um tenant visível para outro via uma fonte que não é realmente pública.

Nada no schema impede isso hoje (`kind` é só uma string) — o enforcement é uma **allowlist fixa de kinds pooláveis** (`rss`, `github-repo`; qualquer `kind` novo entra na allowlist só por decisão explícita), checada na criação do Cadastro. Um `kind` fora da allowlist usa o caminho tenant-scoped original do ADR-0039 (um `item` privado do tenant, sem pool) — a rota de fallback preserva exatamente o desenho anterior para esse caso.

### IV. Dedup também no scheduler, não só no armazenamento

O `kubo/scheduler/sweep.py` passa a iterar sobre `pool_source`, não sobre `source` (Cadastro). Se N tenants têm Cadastro para o mesmo `pool_source`, roda **1 job de coleta**, não N — isso é o que de fato evita o custo duplicado (motivação original do dono), não só a duplicação de linhas no banco.

### V. Backfill automático para tenant que chega depois

Um Cadastro novo (`source`) apontando para um `pool_source` que já tem histórico de `pool_item` coletado por outro tenant **ganha acesso a esse histórico inteiro** — o custo de coleta já foi pago, negar o histórico jogaria fora o ganho principal do pool.

**Consequência para `since`:** o conceito de "piso de estreia por Cadastro" (`since=created_at` do tenant, hoje só em `github-repo`) **deixa de existir por tenant**. `since` passa a ser propriedade do `pool_source` (o momento em que a fonte entrou no pool pela primeira vez, por qualquer tenant) — usado só pelo scheduler para decidir a janela inicial de coleta daquele `pool_source`, não mais recalculado por Cadastro. A idempotência do `upsert_item`/equivalente em `pool_item` continua sendo o mecanismo que evita reprocessar entre runs (como já era).

### VI. Autorização de leitura do pool

Espelhando o princípio do ADR-0039 (autorização via `membership`, não só filtro): ler `pool_item` em nome do tenant X só é legítimo se existir um `source` com `tenant_id=X` e edge `collects` para o `pool_source` daquele `pool_item`. O `kubo/store/` passa a ter **dois caminhos de autorização** — `membership` (tenant↔tenant_id, ADR-0039) e `source→pool_source` (tenant↔conteúdo compartilhado, este ADR) — nenhum dos dois substitui o outro.

## Consequências

- **Positivo:** dedup de custo real (1 job por fonte, não por tenant) — resolve a motivação original, não só duplicação de dado.
- **Positivo:** backfill de graça pro tenant que chega depois é o retorno concreto do investimento em pool compartilhado.
- **Positivo:** reaproveita identidade e lookup-first que já existem e já foram exercitados em produção (`upsert_source`, incidente #105/#106) — não inventa mecanismo novo de identidade.
- **Trade-off:** sem dedup por conteúdo — o mesmo artigo em fontes diferentes continua duplicado. Aceito, ver invariante em I.
- **Trade-off:** allowlist de kinds pooláveis é mais uma coisa para manter disciplinada — um `kind` novo mal-classificado como "público" vazaria dado privado entre tenants. É a superfície de risco mais alta deste ADR.
- **Migração:** dados existentes (`source`/`item` de antes do pivot) precisam ser divididos em `pool_source`+`pool_item` (dado coletado) e `source` novo (Cadastro do "tenant zero", ADR-0039). Execução fica para o Epic de build, não para este ADR.
- **Fog nova:** coleta de lixo (garbage collection) de um `pool_source` cujo último `source` (Cadastro) de tenant foi apagado — quando parar de coletar, quando (se algum dia) apagar o `pool_item` órfão. Anotado no mapa, não decidido aqui.

## Alternativas rejeitadas

- **Dedup por hash de conteúdo** — pegaria mais casos (mesmo artigo em fontes diferentes), mas exige infra de hashing/comparação que não existe hoje; escopo maior que o problema pede agora.
- **Sem dedup no scheduler, só no armazenamento** — resolveria duplicação de linha mas não de custo de coleta/rede, que era a motivação original.
- **Sem backfill pro tenant novo** — mais simples e preserva a semântica atual do `since`, mas joga fora o ganho principal do pool compartilhado.
- **Pool aberto a qualquer `kind`** — mais simples de implementar (sem allowlist), mas vazaria fonte privada/autenticada futura entre tenants. Allowlist explícita foi a escolha.
