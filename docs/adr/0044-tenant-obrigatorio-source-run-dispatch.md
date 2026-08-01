# ADR-0044 — `tenant_id` obrigatório em `source`, `run` e `dispatch`; unicidade por tenant

> Status: proposto · Data: 2026-07-31

## Contexto

O contrato multi-tenant do Kubo foi construído em camadas. O **ADR-0039** estabeleceu o isolamento por `tenant_id` em nível de linha; o **ADR-0041** definiu o superadmin e o tenant zero (`breakglass`); o **ADR-0042** moveu os catálogos para dentro do banco, por tenant. A migração `0020_tenant_contract.surql` cravou `tenant_id` como obrigatório nos domínios centrais do grafo de conhecimento (`flow`, `task`, `distilled`, `entity`, `chunk` e as arestas).

Três tabelas ficaram de fora daquela leva: `source` (Cadastro de fonte, ADR-0025), `run` (execução de worker, ADR-0009) e `dispatch` (fato de entrega, ADR-0015). Não por decisão — por omissão. O código de aplicação passou a gravar `tenant_id` em `run` e `dispatch` durante o épico Cadastro (commit `c4c8a0e`, KUBO-123, 26/07), mas **não** em `source` — esta só passou a gravar `tenant_id` no commit `48fd026` (31/07 16:25). Em todos os casos, o **schema não exigia o campo**: o contrato existia na prática e não no banco. Um caller novo que esquecesse o `tenant_id` gravaria uma linha sem dono, e nada reclamaria.

A migração `0025_dispatch_run_source_tenant.surql` (KUBO-128, entregue via PRs #188/#189/#190 — ver KUBO-145) fechou essa lacuna. Este ADR registra a decisão retroativamente: o código entrou antes do registro, contrariando a regra do `CLAUDE.md` de que mudança estrutural vira ADR **antes** do código.

> Aprovação retroativa do owner para esta exceção: registrada no PR #191 (merged em 31/07 19:01). A exceção é aceita porque o ADR é puramente documental e não altera o contrato já aplicado.

## Decisão

**1. `tenant_id` é obrigatório em `source`, `run` e `dispatch`.**
`DEFINE FIELD OVERWRITE tenant_id ... TYPE record<tenant> ASSERT $value != NONE`. Linha sem tenant deixa de ser representável nessas tabelas — o banco passa a recusar o que antes só a disciplina do caller evitava.

**2. `tenant_id` é opcional em `destination`, deliberadamente e em caráter temporário.**
`TYPE option<record<tenant>>`. A assimetria é rollout suave: nem todos os callers de `destination` foram migrados quando a 0025 entrou. O `ASSERT` vem quando forem — é dívida nomeada, com dono (follow-up de KUBO-128; ticket específico a ser criado), não um descuido.

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
- O código anterior ao commit `48fd026` **não** gravava `tenant_id` em `source` — a tabela era global por decisão (KUBO-123). Logo, no momento da migração existiam sim linhas sem tenant: o backfill tinha alvo. O commit `48fd026` (que tornou `tenant_id` obrigatório em `create_source`/`upsert_source` e passou a gravá-lo) é de 31/07 16:25; as 55 `source` de PRD foram criadas em 25/07 — portanto **antes** dele, sem `tenant_id`.
- **DEV** (`kubo-test`): 179 `source` criadas entre 05/07 e 21/07; `0025` aplicada em 31/07 20:33; **todas** em `tenant:breakglass`. Coerente com o backfill tendo disparado.
- **PRD**: 55 `source` criadas em 25/07 (ids surrogate de 32 caracteres, `created_at` é `READONLY`); `0025` aplicada em 31/07 20:35; **zero** em `breakglass`, todas no tenant pessoal do dono — que só passou a existir em 31/07, depois do reset de identidade. O mesmo vale para `run` (319), `dispatch` (11) e o acervo de conhecimento (`item` 5948, `distilled`, `entity`, `chunk`): nada em `breakglass`.

**Em aberto:** por que o PRD não tem uma única linha em `breakglass` se, na hora da migração, aquelas linhas não tinham tenant. Os candidatos óbvios foram descartados por leitura de código: o script de reset de identidade não toca `source`/`run`/`dispatch`; `upsert_source` e `upsert_seed_source` fazem lookup **por tenant**, então re-semear criaria linhas novas em vez de mover as existentes — e o total permaneceu 55. Enquanto isso não for explicado, **não se pode afirmar nem que o backfill é linha morta, nem que o destino `breakglass` é seguro** — os dois ambientes se comportaram de forma diferente e só um deles foi entendido.

Consequência prática: o ADR não pode ser aceito, e a pergunta continua no KUBO-146.

> **Revisado pelo advisor** (fable-advisor, 31/07): a decisão estrutural (pontos 1–4) está correta e bem justificada. O status `proposto` é adequado enquanto a questão do backfill não for resolvida com evidência direta. Correções aplicadas: (a) inconsistência Contexto vs Estabelecido sobre quando `source` passou a gravar `tenant_id`; (b) adição do replay em sandbox como experimento decisivo; (c) aprovação retroativa referenciando o PR #191 merged; (d) generalização da pista SCHEMAFULL para todas as três tabelas; (e) verificação de execução dos `UPDATE` e confirmação de versão do SurrealDB adicionadas aos próximos passos.

#### Pistas adicionais (investigação de código, 31/07)

A investigação posterior ao primeiro code-review encontrou três pistas que afunilam a pergunta, mas não a fecham:

1. **Sequência de deploy** (`scripts/deploy-remote.sh`): o deploy roda `stop writers → migrations → seed → start writers`. O seed (`python -m kubo.store.seed`) roda **depois** da `0025` e resolve o tenant pessoal via `resolve_scheduler_tenant_and_user`. Se o marcador `feed_cadastros` já existir, o seed não cria nada; se não existir, cria 6 fontes sob o tenant pessoal. Nenhum dos dois cenários explica as 55 fontes existentes mudarem de tenant.

2. **`source` é SCHEMAFULL** (`migration 0001`): antes da `0025`, o campo `tenant_id` não estava definido no schema de `source` — nem de `run` ou `dispatch`. Em uma tabela SCHEMAFULL, campos não definidos são descartados silenciosamente na escrita: mesmo que algum código pré-0025 tentasse gravar `tenant_id`, o campo não persistiria. Logo, **todas** as linhas pré-0025 estavam sem o campo, em ambos os ambientes. A `0025` faz `DEFINE FIELD OVERWRITE` (adiciona o campo ao schema) e logo depois `UPDATE ... WHERE tenant_id IS NONE`. Em tese, `IS NONE` cobre campo ausente em SurrealDB; na prática, DEV (mesmo código, mesma versão de servidor) backfillou 179 — então o mecanismo funciona. A diferença entre DEV e PRD não está no código da migração. A divergência é ainda mais anômala por isso: se todas as linhas estavam sem o campo em ambos os ambientes, algo **depois** da migração deve ter reescrito as linhas em PRD — o "reset de identidade" é o suspeito natural.

3. **`neon_import.py` chama `upsert_source` sem `tenant_id`** (linha 639): `knowledge.upsert_source(db, kind=kind, canonical=canonical, title=title)`. Após `48fd026`, essa chamada quebraria (TypeError: missing required keyword argument `tenant_id`). O script não foi atualizado desde `73cc829` (início de julho). Isso confirma que o import rodou com código antigo (sem `tenant_id`) e não pode ter sido re-execuído depois.

#### Próximos passos para resolver (KUBO-146)

A pergunta precisa de evidência direta do banco de PRD, não de dedução. Em ordem de força:

1. **Replay em sandbox (experimento decisivo):** restaurar o snapshot de PRD pré-31/07 (sidecar de backup) num sandbox isolado e reaplicar a sequência exata do deploy (`migrations → seed`). Se o replay reproduzir zero em `breakglass`, a causa é mecânica e o ADR pode ir a `aceito` com a explicação. Se o replay reproduzir o comportamento de DEV (backfill funcionando), a causa é externa à migração (reset de identidade) e o "Em aberto" vira post-mortem do reset.
2. **Confirmar que os `UPDATE` da 0025 executaram em PRD:** "migração marcada como aplicada" ≠ "backfill executou" — o runner envolve tudo em `BEGIN/COMMIT`, mas vale verificar se houve erro parcial nos statements multi-linha. Inspecionar `SELECT * FROM migration WHERE name = '0025_dispatch_run_source_tenant.surql'` e logs do deploy.
3. **Confirmar a versão do SurrealDB em PRD:** o ADR assume mesma versão que DEV (`v3.1.5`), mas não cita a medição. Rodar `INFO FOR DB` ou verificar o `docker-compose.yml` pinado.
4. **Queries diretas no banco de PRD:** `SELECT count() FROM source GROUP BY tenant_id` (distribuição atual); `SELECT id, tenant_id, created_at FROM source ORDER BY created_at LIMIT 5` (verificar se `tenant_id` está realmente preenchido).
5. **Inspecionar o "reset de identidade" (31/07):** o que fez na prática — `REMOVE DATABASE` + re-import? `UPDATE` em alguma tabela? Se o banco foi re-criado, `created_at` READONLY pode ter sido preservado pelo import.

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
