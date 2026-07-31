# ADR-0044 — `tenant_id` obrigatório em `source`, `run` e `dispatch`; unicidade por tenant

> Status: proposto · Data: 2026-07-31

## Contexto

O contrato multi-tenant do Kubo foi construído em camadas. O **ADR-0039** estabeleceu o isolamento por `tenant_id` em nível de linha; o **ADR-0041** definiu o superadmin e o tenant zero (`breakglass`); o **ADR-0042** moveu os catálogos para dentro do banco, por tenant. A migração `0020_tenant_contract.surql` cravou `tenant_id` como obrigatório nos domínios centrais do grafo de conhecimento (`flow`, `task`, `distilled`, `entity`, `chunk` e as arestas).

Três tabelas ficaram de fora daquela leva: `source` (Cadastro de fonte, ADR-0025), `run` (execução de worker, ADR-0009) e `dispatch` (fato de entrega, ADR-0015). Não por decisão — por omissão. O código de aplicação passou a gravar `tenant_id` nessas tabelas durante o épico Cadastro (`kubo/store/knowledge.py`), mas o **schema não exigia o campo**. O contrato existia na prática e não no banco: um caller novo que esquecesse o `tenant_id` gravaria uma linha sem dono, e nada reclamaria.

A migração `0025_dispatch_run_source_tenant.surql` (KUBO-128, entregue via PRs #188/#189/#190 — ver KUBO-145) fechou essa lacuna. Este ADR registra a decisão retroativamente: o código entrou antes do registro, contrariando a regra do `CLAUDE.md` de que mudança estrutural vira ADR **antes** do código.

> Aprovação retroativa do owner para esta exceção: a ser anexada ao PR #191 antes do merge.

## Decisão

**1. `tenant_id` é obrigatório em `source`, `run` e `dispatch`.**
`DEFINE FIELD OVERWRITE tenant_id ... TYPE record<tenant> ASSERT $value != NONE`. Linha sem tenant deixa de ser representável nessas tabelas — o banco passa a recusar o que antes só a disciplina do caller evitava.

**2. `tenant_id` é opcional em `destination`, deliberadamente e em caráter temporário.**
`TYPE option<record<tenant>>`. A assimetria é rollout suave: nem todos os callers de `destination` foram migrados quando a 0025 entrou. O `ASSERT` vem quando forem — é dívida nomeada, com dono (follow-up de KUBO-128), não um descuido.

**3. Unicidade passa a ser por tenant.**
`source`: `(tenant_id, kind, canonical)`. `destination`: `(tenant_id, channel, address)`. Dois tenants podem cadastrar a mesma URL de feed ou o mesmo endereço de destino, cada um com seu registro. A unicidade global — que fazia sentido quando o Kubo era mono-usuário — passaria a ser um vazamento: o segundo tenant a cadastrar uma fonte popular descobriria que ela "já existe" e é de outra pessoa.

**4. O `upsert_source` lookup-first continua correto sob a chave nova.**
Ele já busca por `(tenant_id, kind, canonical)` antes de decidir entre reusar e criar (`kubo/store/knowledge.py:150-181`, função `upsert_source`). O índice novo é exatamente a chave que a busca usa; o desarme da colisão entre os dois escritores (coletor e UI), estabelecido no épico Cadastro, sobrevive intacto.

**5. O destino do backfill (`tenant:breakglass`) fica como está — mas o seu efeito real segue EM ABERTO.**
Este ponto é o motivo de o ADR estar `proposto` e não `aceito`. A migração aplicada não se reescreve de qualquer forma; o que falta não é uma decisão, é entender o que de fato aconteceu com os dados (ver abaixo).

## Consequências

**Positivas.** O contrato multi-tenant deixa de depender de disciplina de caller nas três tabelas mais movimentadas do produto. Um worker novo que esqueça o `tenant_id` falha na escrita, não em produção seis meses depois com dados de dois donos misturados. A unicidade por tenant destrava o cenário multi-usuário real — que era o ponto do ADR-0039.

**Negativas.** `destination` fica com contrato mais fraco que suas irmãs até o follow-up. Enquanto durar, é a única das quatro onde uma linha sem dono ainda é representável — e a assimetria precisa ser lembrada por quem mexer nela.

**Neutras.** Ambientes existentes não precisaram de intervenção manual: a DDL entrou e as linhas legadas sem `tenant_id` foram regularizadas pelo backfill — em DEV para `tenant:breakglass`, conforme as contagens abaixo. Não se afirma que os dados já estavam conformes antes da DDL.

### O que a investigação estabeleceu — e o que ficou em aberto

O texto da migração diz que dados legados vão para o tenant administrativo. Em PRD, **não foi isso que se observou**, e a explicação ainda não fecha. Registrado como pergunta aberta em vez de conclusão bonita, porque o critério de aceite do KUBO-146 é evidência, não dedução — e a dedução é justamente o que falhou aqui duas vezes.

**Estabelecido (medido):**

- A `0025` é a única migração que declara `tenant_id` em `source`/`run`/`dispatch`.
- O código anterior ao commit `48fd026` **não** gravava `tenant_id` em `source` — a tabela era global por decisão (KUBO-123). Logo, no momento da migração existiam sim linhas sem tenant: o backfill tinha alvo.
- **DEV** (`kubo-test`): 179 `source` criadas entre 05/07 e 21/07; `0025` aplicada em 31/07 20:33; **todas** em `tenant:breakglass`. Coerente com o backfill tendo disparado.
- **PRD**: 55 `source` criadas em 25/07 (ids surrogate de 32 caracteres, `created_at` é `READONLY`); `0025` aplicada em 31/07 20:35; **zero** em `breakglass`, todas no tenant pessoal do dono — que só passou a existir em 31/07, depois do reset de identidade. O mesmo vale para `run` (319), `dispatch` (11) e o acervo de conhecimento (`item` 5948, `distilled`, `entity`, `chunk`): nada em `breakglass`.

**Em aberto:** por que o PRD não tem uma única linha em `breakglass` se, na hora da migração, aquelas linhas não tinham tenant. Os candidatos óbvios foram descartados por leitura de código: o script de reset de identidade não toca `source`/`run`/`dispatch`; `upsert_source` e `upsert_seed_source` fazem lookup **por tenant**, então re-semear criaria linhas novas em vez de mover as existentes — e o total permaneceu 55. Enquanto isso não for explicado, **não se pode afirmar nem que o backfill é linha morta, nem que o destino `breakglass` é seguro** — os dois ambientes se comportaram de forma diferente e só um deles foi entendido.

Consequência prática: o ADR não pode ser aceito, e a pergunta continua no KUBO-146.

#### Artefatos e consultas (reprodução)

As contagens, timestamps e conclusões desta seção foram coletados com consultas **read-only** ao SurrealDB de cada ambiente, imediatamente após a aplicação da `0025` em 2026-07-31:

- DEV (`kubo-test`): `SELECT count() FROM source WHERE tenant_id = tenant:breakglass` retornou 179; `0025` aplicada em 31/07 20:33.
- PRD: `SELECT count() FROM source WHERE tenant_id = tenant:breakglass` retornou 0; `0025` aplicada em 31/07 20:35; total de `source` → 55, `run` → 319, `dispatch` → 11.
- Migração: `store/migrations/0025_dispatch_run_source_tenant.surql` é a única que declara `tenant_id` em `source`/`run`/`dispatch` (confirmado por `rg 'tenant_id' store/migrations/`).
- Histórico de `source`: `git log --follow -p -- kubo/store/knowledge.py` até o commit `48fd026` mostra `tenant_id` ausente nos parâmetros/escrita de `create_source`/`upsert_source` — o que indica que linhas legadas sem `tenant_id` existiam no momento da `0025`.
- Reset de identidade: inspecionado em `kubo/store/knowledge.py` e scripts de reset; nenhuma operação de atualização em massa de `source`/`run`/`dispatch` foi encontrada.

#### Evidência de ausência de expurgo de `dispatch`

- Busca por job de expurgo: `rg -i 'expurg|prune|delete.*dispatch|remove.*dispatch|truncate.*dispatch' kubo/scheduler kubo/workers kubo/distribution kubo/runtime` retornou 0 ocorrências.
- Série PRD: `SELECT * FROM dispatch ORDER BY created_at` cobre 25/07–31/07; o padrão de duas entregas diárias e a lacuna de 30/07 são consistentes com os dias ativos de execução e o incidente de crash-loop já documentado.

**Registro adjacente, para não virar folclore:** levantou-se a suspeita de perda de registros em `dispatch` (uma contagem anterior de 25 contra as 11 atuais). **Não se confirmou.** Não existe job de expurgo de `dispatch` em nenhum ponto do código, e os 11 registros cobrem 25/07 a 31/07 num padrão coerente de duas entregas diárias (Telegram + e-mail), com a lacuna do dia 30 correspondendo ao incidente de crash-loop já documentado. A contagem anterior era medição malfeita, não dado sumido.

## Alternativas rejeitadas

- **Manter a unicidade global de `source`** — transformaria a primeira fonte cadastrada num squat: o segundo tenant não conseguiria cadastrar a mesma URL.
- **Tornar `tenant_id` obrigatório também em `destination` na mesma migração** — quebraria callers ainda não migrados no momento do deploy; o ganho não paga o risco de um rollout de uma tacada só.
- **Backfill apontando para o primeiro tenant em vez do administrativo** — resolveria um problema que não existe, e faria a migração depender de ordenação de ids, que não é contrato de nada.
- **Remover o backfill morto da migração já aplicada** — reescrever migração aplicada é pior que conviver com três linhas inertes.
