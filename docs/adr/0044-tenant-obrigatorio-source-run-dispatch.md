# ADR-0044 — `tenant_id` obrigatório em `source`, `run` e `dispatch`; unicidade por tenant

> Status: aceito · Data: 2026-07-31

## Contexto

O contrato multi-tenant do Kubo foi construído em camadas. O **ADR-0039** estabeleceu o isolamento por `tenant_id` em nível de linha; o **ADR-0041** definiu o superadmin e o tenant zero (`breakglass`); o **ADR-0042** moveu os catálogos para dentro do banco, por tenant. A migração `0020_tenant_contract.surql` cravou `tenant_id` como obrigatório nos domínios centrais do grafo de conhecimento (`flow`, `task`, `distilled`, `entity`, `chunk` e as arestas).

Três tabelas ficaram de fora daquela leva: `source` (Cadastro de fonte, ADR-0025), `run` (execução de worker, ADR-0009) e `dispatch` (fato de entrega, ADR-0015). Não por decisão — por omissão. O código de aplicação passou a gravar `tenant_id` nessas tabelas durante o épico Cadastro (`kubo/store/knowledge.py`), mas o **schema não exigia o campo**. O contrato existia na prática e não no banco: um caller novo que esquecesse o `tenant_id` gravaria uma linha sem dono, e nada reclamaria.

A migração `0025_dispatch_run_source_tenant.surql` (KUBO-128, entregue via PRs #188/#189/#190 — ver KUBO-145) fechou essa lacuna. Este ADR registra a decisão retroativamente: o código entrou antes do registro, contrariando a regra do `CLAUDE.md` de que mudança estrutural vira ADR **antes** do código.

## Decisão

**1. `tenant_id` é obrigatório em `source`, `run` e `dispatch`.**
`DEFINE FIELD OVERWRITE tenant_id ... TYPE record<tenant> ASSERT $value != NONE`. Linha sem tenant deixa de ser representável nessas tabelas — o banco passa a recusar o que antes só a disciplina do caller evitava.

**2. `tenant_id` é opcional em `destination`, deliberadamente e em caráter temporário.**
`TYPE option<record<tenant>>`. A assimetria é rollout suave: nem todos os callers de `destination` foram migrados quando a 0025 entrou. O `ASSERT` vem quando forem — é dívida nomeada, com dono (follow-up de KUBO-128), não um descuido.

**3. Unicidade passa a ser por tenant.**
`source`: `(tenant_id, kind, canonical)`. `destination`: `(tenant_id, channel, address)`. Dois tenants podem cadastrar a mesma URL de feed ou o mesmo endereço de destino, cada um com seu registro. A unicidade global — que fazia sentido quando o Kubo era mono-usuário — passaria a ser um vazamento: o segundo tenant a cadastrar uma fonte popular descobriria que ela "já existe" e é de outra pessoa.

**4. O `upsert_source` lookup-first continua correto sob a chave nova.**
Ele já busca por `(tenant_id, kind, canonical)` antes de decidir entre reusar e criar (`knowledge.py:98-109`). O índice novo é exatamente a chave que a busca usa; o desarme da colisão entre os dois escritores (coletor e UI), estabelecido no épico Cadastro, sobrevive intacto.

**5. O backfill para `tenant:breakglass` é reconhecido como linha morta e fica como está.**
A migração contém `UPDATE ... SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE`. Ele nunca atualizou nenhuma linha e não tem como atualizar (ver abaixo). Não removemos: uma migração aplicada não se reescreve, e o custo de mantê-la é zero.

## Consequências

**Positivas.** O contrato multi-tenant deixa de depender de disciplina de caller nas três tabelas mais movimentadas do produto. Um worker novo que esqueça o `tenant_id` falha na escrita, não em produção seis meses depois com dados de dois donos misturados. A unicidade por tenant destrava o cenário multi-usuário real — que era o ponto do ADR-0039.

**Negativas.** `destination` fica com contrato mais fraco que suas irmãs até o follow-up. Enquanto durar, é a única das quatro onde uma linha sem dono ainda é representável — e a assimetria precisa ser lembrada por quem mexer nela.

**Neutras.** Ambientes existentes não precisaram de intervenção manual: a DDL entrou sobre dados já conformes.

### A verificação que sustenta o ponto 5

O texto da migração diz que dados legados vão para o tenant administrativo. Isso **não descreve o que aconteceu**, e a diferença importa o bastante para ficar registrada — foi investigada com evidência, não deduzida (critério de aceite do KUBO-146):

- **PRD** (2026-07-31, pós-migração): `source` 55, `dispatch` 11, `run` 319, `destination` 3. Linhas em `tenant:breakglass`: **zero**. Linhas sem tenant: **zero**. Tudo sob o tenant pessoal do dono.
- **DEV** (`kubo-test`): `source` 179, `run` 782, `dispatch` 5, `destination` 1 — todas sob `breakglass`, que é o tenant legítimo daquele ambiente. Sem tenant: **zero**.

A causa é o ponto de partida deste ADR: o código já gravava `tenant_id` antes de o schema exigir. Quando a DDL chegou, não havia linha órfã. E o cenário de risco que se imagina ao ler o backfill — "num ambiente novo, a base de coleta inteira vai para a conta administrativa" — **não é alcançável**: ambiente novo nasce com as tabelas vazias, e os dois ambientes que existem já migraram sem nenhuma linha órfã. Não há terceiro caso.

Registro adjacente, para não virar folclore: durante a investigação levantou-se a suspeita de perda de registros em `dispatch` (uma contagem anterior de 25 contra as 11 atuais). **Não se confirmou.** Não existe job de expurgo de `dispatch` em nenhum ponto do código, e os 11 registros cobrem 25/07 a 31/07 num padrão coerente de duas entregas diárias (Telegram + e-mail), com a lacuna do dia 30 correspondendo ao incidente de crash-loop já documentado. A contagem anterior era medição malfeita, não dado sumido.

## Alternativas rejeitadas

- **Manter a unicidade global de `source`** — transformaria a primeira fonte cadastrada num squat: o segundo tenant não conseguiria cadastrar a mesma URL.
- **Tornar `tenant_id` obrigatório também em `destination` na mesma migração** — quebraria callers ainda não migrados no momento do deploy; o ganho não paga o risco de um rollout de uma tacada só.
- **Backfill apontando para o primeiro tenant em vez do administrativo** — resolveria um problema que não existe, e faria a migração depender de ordenação de ids, que não é contrato de nada.
- **Remover o backfill morto da migração já aplicada** — reescrever migração aplicada é pior que conviver com três linhas inertes.
