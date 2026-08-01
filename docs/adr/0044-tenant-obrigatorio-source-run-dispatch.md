# ADR-0044 — `tenant_id` obrigatório em `source`, `run` e `dispatch`; unicidade por tenant

> Status: aceito · Data: 2026-07-31

## Contexto

O contrato multi-tenant do Kubo foi construído em camadas. O **ADR-0039** estabeleceu o isolamento por `tenant_id` em nível de linha; o **ADR-0041** definiu o superadmin e o tenant zero (`breakglass`); o **ADR-0042** moveu os catálogos para dentro do banco, por tenant. A migração `0020_tenant_contract.surql` cravou `tenant_id` como obrigatório nos domínios centrais do grafo de conhecimento (`flow`, `task`, `distilled`, `entity`, `chunk` e as arestas).

Três tabelas ficaram de fora daquela leva: `source` (Cadastro de fonte, ADR-0025), `run` (execução de worker, ADR-0009) e `dispatch` (fato de entrega, ADR-0015). Não por decisão — por omissão. O código de aplicação passou a gravar `tenant_id` nessas tabelas durante o épico Cadastro (`kubo/store/knowledge.py`), mas o **schema não exigia o campo**. O contrato existia na prática e não no banco: um caller novo que esquecesse o `tenant_id` gravaria uma linha sem dono, e nada reclamaria.

A migração `0025_dispatch_run_source_tenant.surql` (KUBO-128, entregue via PRs #188/#189/#190 — ver KUBO-145) fechou essa lacuna. Este ADR registra a decisão retroativamente: o código entrou antes do registro, contrariando a regra do `CLAUDE.md` de que mudança estrutural vira ADR **antes** do código.

> Aprovação retroativa do owner para esta exceção: registrada no PR #191, que fez o merge do ADR e das correções de DDL. A exceção é aceita porque o ADR é puramente documental e não altera o contrato já aplicado.

## Decisão

**1. `tenant_id` é obrigatório em `source`, `run` e `dispatch`.**
`DEFINE FIELD OVERWRITE tenant_id ... TYPE record<tenant> ASSERT $value != NONE`. Linha sem tenant deixa de ser representável nessas tabelas — o banco passa a recusar o que antes só a disciplina do caller evitava.

**2. `tenant_id` é opcional em `destination`, deliberadamente e em caráter temporário.**
`TYPE option<record<tenant>>`. A assimetria é rollout suave: nem todos os callers de `destination` foram migrados quando a 0025 entrou. O `ASSERT` vem quando forem — é dívida nomeada, com dono (follow-up de KUBO-128), não um descuido.

**3. Unicidade passa a ser por tenant.**
`source`: `(tenant_id, kind, canonical)`. `destination`: `(tenant_id, channel, address)`. Dois tenants podem cadastrar a mesma URL de feed ou o mesmo endereço de destino, cada um com seu registro. A unicidade global — que fazia sentido quando o Kubo era mono-usuário — passaria a ser um vazamento: o segundo tenant a cadastrar uma fonte popular descobriria que ela "já existe" e é de outra pessoa.

**4. O `upsert_source` lookup-first continua correto sob a chave nova.**
Ele já busca por `(tenant_id, kind, canonical)` antes de decidir entre reusar e criar (`kubo/store/knowledge.py:150-181`, função `upsert_source`). O índice novo é exatamente a chave que a busca usa; o desarme da colisão entre os dois escritores (coletor e UI), estabelecido no épico Cadastro, sobrevive intacto.

**5. O destino do backfill (`tenant:breakglass`) fica como está — e o seu efeito foi esclarecido pelo KUBO-146 (ver abaixo).**

## Consequências

**Positivas.** O contrato multi-tenant deixa de depender de disciplina de caller nas três tabelas mais movimentadas do produto. Um worker novo que esqueça o `tenant_id` falha na escrita, não em produção seis meses depois com dados de dois donos misturados. A unicidade por tenant destrava o cenário multi-usuário real — que era o ponto do ADR-0039.

**Negativas.** `destination` fica com contrato mais fraco que suas irmãs até o follow-up. Enquanto durar, é a única das quatro onde uma linha sem dono ainda é representável — e a assimetria precisa ser lembrada por quem mexer nela.

**Neutras.** Ambientes existentes não precisaram de intervenção manual: a DDL entrou e as linhas legadas sem `tenant_id` foram regularizadas pelo backfill — em DEV para `tenant:breakglass`, conforme as contagens abaixo. Em PRD o backfill foi um no-op porque as linhas legadas já tinham `tenant_id`.

### O que a investigação estabeleceu — resolução do KUBO-146

A migração `0025` faz **apenas uma** coisa em massa: `UPDATE <tabela> SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE`. Se a condição `IS NONE` fosse verdadeira para qualquer linha de `source`, `run` ou `dispatch` em PRD, essas linhas estariam em `tenant:breakglass` após a migração. A contagem direta no SurrealDB, imediatamente após a `0025` em 2026-07-31, retornou **zero** em `tenant:breakglass` para todas essas tabelas. Isso é evidência medida, não dedução: o `WHERE tenant_id IS NONE` não encontrou linhas em PRD.

**Estabelecido (medido):**

- A `0025` é a única migração que declara `tenant_id` em `source`/`run`/`dispatch`.
- **DEV** (`kubo-test`): 179 `source` criadas entre 05/07 e 21/07; `0025` aplicada em 31/07 20:33; **todas** em `tenant:breakglass`. Coerente com o backfill tendo disparado porque essas linhas foram escritas antes do rollout do `tenant_id` e estavam com o campo vazio.
- **PRD**: 55 `source` criadas em 25/07; `0025` aplicada em 31/07 20:35; **zero** em `breakglass`. O mesmo vale para `run` (319), `dispatch` (11) e o acervo de conhecimento (`item` 5948, `distilled`, `entity`, `chunk`): nada em `breakglass`. Isso indica que, no momento da `0025`, essas linhas já carregavam um `tenant_id` preenchido.
- **Não existe** nenhum `UPDATE` massivo de `tenant_id` em `source`/`run`/`dispatch` fora a própria `0025` — inspeção de `kubo/store/knowledge.py`, `kubo/store/destinations.py`, `kubo/store/flows.py` e scripts de reset não encontrou nenhuma operação de reatribuição. Portanto a leitura (b) — "algo depois do backfill reatribuiu os registros" — não tem suporte no código.

**Resolução:**

- A leitura **(a)** é a verdadeira para PRD: as linhas de `source`/`run`/`dispatch` já tinham `tenant_id` quando a `0025` foi aplicada, então o backfill `WHERE tenant_id IS NONE` não disparou para elas. A migração funcionou exatamente como especificado; apenas não teve alvo.
- O destino `tenant:breakglass` fica como está. Ele é a queda de segurança correta para qualquer ambiente que ainda tenha linhas sem `tenant_id` quando a `0025` rodar. Reescrever uma migração já aplicada seria pior do que manter as três linhas de fallback.
- A queda de `dispatch` 25 → 11 é uma medição anterior ruim, não perda de dados: os 11 registros cobrem 25/07 a 31/07 num padrão coerente de duas entregas diárias, com a lacuna do dia 30 correspondendo ao incidente de crash-loop já documentado. Não há job de expurgo de `dispatch` no código.

#### Artefatos e consultas (reprodução)

As contagens, timestamps e conclusões desta seção foram coletados com consultas **read-only** ao SurrealDB de cada ambiente, imediatamente após a aplicação da `0025` em 2026-07-31:

- DEV (`kubo-test`): `SELECT count() FROM source WHERE tenant_id = tenant:breakglass` retornou 179; `0025` aplicada em 31/07 20:33.
- PRD: `SELECT count() FROM source WHERE tenant_id = tenant:breakglass` retornou 0; `0025` aplicada em 31/07 20:35; total de `source` → 55, `run` → 319, `dispatch` → 11.
- Migração: `store/migrations/0025_dispatch_run_source_tenant.surql` é a única que declara `tenant_id` em `source`/`run`/`dispatch` (confirmado por `rg 'tenant_id' store/migrations/`).
- Busca por reatribuição: `rg -i 'UPDATE.*tenant_id|SET tenant_id' kubo/store kubo/scheduler kubo/workers kubo/distribution kubo/runtime` mostra que a única operação massiva é a própria `0025`.

## Alternativas rejeitadas

- **Manter a unicidade global de `source`** — transformaria a primeira fonte cadastrada num squat: o segundo tenant não conseguiria cadastrar a mesma URL.
- **Tornar `tenant_id` obrigatório também em `destination` na mesma migração** — quebraria callers ainda não migrados no momento do deploy; o ganho não paga o risco de um rollout de uma tacada só.
- **Backfill apontando para o primeiro tenant em vez do administrativo** — resolveria um problema que não existe, e faria a migração depender de ordenação de ids, que não é contrato de nada.
- **Remover o backfill morto da migração já aplicada** — reescrever migração aplicada é pior que conviver com três linhas inertes.
