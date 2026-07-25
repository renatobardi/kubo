# ADR-0039 — Modelo de dados de tenancy (isolamento row-level + credenciais de tenant cifradas)

> Status: **aceito** · Data: 2026-07-25 · Emenda os invariantes 2, 3 e 8 do CLAUDE.md e a spec funcional §1.2/§5 (reescrita formal fica a cargo de [KUBO-110](https://oute.atlassian.net/browse/KUBO-110)).

## Contexto

O Kubo pivota de "ateliê pessoal de mantenedor solo" (spec §1.2: "não é plataforma multi-tenant. Um dono, uma VPC") para multi-tenant real, escalando como produto. Decisão registrada no mapa wayfinder [KUBO-104](https://oute.atlassian.net/browse/KUBO-104); este ADR resolve o ticket-frontier [KUBO-105](https://oute.atlassian.net/browse/KUBO-105) — o modelo de dados do qual quase todo o resto do mapa depende.

Nenhuma tabela do schema atual (`docs/kubo-spec-funcional.md` §2.3, migrations `0001`–`0014`) tem `tenant_id`/`owner_id`. `persona` é papel de agente, não identidade humana.

Decisões já fechadas no charting (KUBO-104) e assumidas aqui sem reabrir: self-signup aberto via Firebase; workspace compartilhado dentro do tenant (sem isolamento intra-tenant); BYOK obrigatório; isolamento row-level via `tenant_id` (não NS/DB por tenant); catálogos por-tenant no banco com changelog de auditoria.

## Decisão

### I. Tabelas `tenant`, `user`, `membership`

- **`tenant`**: entidade de equipe/grupo. Campos mínimos: `id`, `name`, `created_at`.
- **`user`**: identidade humana (distinta de `persona`, que é papel de agente). Campos mínimos: `id`, `firebase_uid`, `email`, `created_at`. Vive fora de qualquer tenant — não tem `tenant_id` próprio.
- **`membership`** (edge `user -> tenant`): relação N:N. Um usuário pode pertencer a vários tenants (ex.: dono do próprio tenant pessoal + membro do tenant de um amigo). Campos: `user`, `tenant`, `role` (`owner` | `member`), `created_at`.
  - **Exatamente 1 `owner` por tenant**: verificado em código no `kubo/store/` no momento de criar/transferir membership — não índice único parcial no SurrealDB (suporte não confirmado na v3.1.5 pinada pelo ADR-0005; um `if` no store resolve sem pesquisa adicional).

O fluxo de auth por cima destas tabelas (self-signup cria `user`+`tenant`+`membership(role=owner)`; convite de equipe usa tabela nova, distinta do `invite` do ADR-0033; mapeamento Firebase→`user`) é escopo do ADR de [KUBO-108](https://oute.atlassian.net/browse/KUBO-108), não deste.

### II. Tenant ativo por sessão + autorização no store

A sessão guarda **1 workspace ativo** (`tenant_id`) — usuário troca de tenant via seletor na UI, padrão GitHub/Slack. Mecanismo de troca é escopo do KUBO-108; o que este ADR fixa é a garantia no `kubo/store/`:

**Toda operação tenant-scoped no `kubo/store/` exige uma linha de `membership` válida para `(user_id, tenant_id)` antes de executar** — o store rejeita se a sessão apontar para um tenant ao qual o usuário não pertence. Isso fecha o risco de uma sessão adulterada ler dados de outro tenant só porque o filtro de query aceitou o valor recebido sem checar autorização; a checagem de membership é o mecanismo de enforcement, não um detalhe de UI.

### III. `tenant_id` aplicado às tabelas existentes — regra dos dois caminhos

Toda tabela do Kubo é, sem exceção, **ou** 100% tenant-scoped **ou** 100% pool compartilhado — nunca as duas coisas na mesma tabela (sem `tenant_id` anulável):

- **Tabelas tenant-scoped** (`source, item, distilled, entity, memory, flow, flow_template, board_state, task, deliverable, git_repo, persona`, catálogos por-tenant, `tenant_credential`, etc.): ganham `tenant_id` **obrigatório**, aplicado e filtrado inteiramente dentro do `kubo/store/` (invariante 2 — acesso a banco centralizado; nenhuma query espalhada fora dessa camada).
- **Tabelas de pool compartilhado**: conteúdo bruto coletado de fontes públicas (RSS, GitHub público, futuro webscraping), potencialmente reaproveitado entre tenants para evitar coleta/custo duplicado. Vivem em tabela(s) físicas próprias, **sem `tenant_id` em nenhuma linha**; tabelas do tenant apontam para elas via edge quando aplicável.
  - **A chave de identidade do pool (hash de conteúdo, canonical id) e o mecanismo de dedup em si NÃO são definidos aqui** — a research de [KUBO-106](https://oute.atlassian.net/browse/KUBO-106) já levantou que `item` hoje não tem hash de conteúdo e a normalização canônica cobre só o Cadastro, não `item.url`/`external_id`. O design do pool fica para [KUBO-107](https://oute.atlassian.net/browse/KUBO-107), que consome esta regra dos dois caminhos como restrição de forma, não a redecide.

**Enforcement é só de aplicação** (código Python no `kubo/store/`), não nativo do SurrealDB: a v3.1.5 pinada suporta `PERMISSIONS ... WHERE $auth.tenant_id = ...` como row-level security nativa, mas isso só vale para "record users" (autenticados via `DEFINE ACCESS`) — usuários de sistema (root/`kubo_rw`, usados hoje pelo `kubo/store/`) sempre bypassam. Adotar RLS nativo exigiria trocar o modelo de conexão inteiro (de pool root/`kubo_rw` para autenticação por record-user); isso fica como hardening futuro, não decisão deste ADR (ver Not yet specified do mapa KUBO-104).

### IV. Credenciais de tenant

Tabela própria **`tenant_credential`** (`tenant_id`, `provider`, valor cifrado, metadados) — um tenant tem N keys (OpenAI, Anthropic, Groq, GitHub token, etc.), cada uma sua própria linha, rotacionável/revogável isoladamente.

Criptografia: **Fernet** (pacote `cryptography`, já disponível como dependência transitiva de `pyjwt[crypto]` — sem dependência nova). Chave mestra de 32 bytes via env (`KUBO_TENANT_CREDENTIAL_KEY` ou similar). **Emenda formal ao invariante 8**: segredo de sistema continua só-env; segredo de tenant passa a ser dado cifrado em repouso no banco único, com a chave mestra (não o segredo em si) vivendo em env.

### V. Migração dos dados existentes ("tenant zero")

O dono (Renato) e seus dados existentes viram **um tenant comum, igual a qualquer outro** — criado pelo script de migração (linha `tenant` + `user` + `membership(role=owner)` + backfill de `tenant_id` nas tabelas existentes), sem nenhum caso especial no código (`if tenant == zero` não deve existir em lugar nenhum). Execução (script, migration `.surql` sequencial após `0014`) fica para o Epic de build, não para este ADR.

## Consequências

- **Positivo:** regra dos dois caminhos (tenant-scoped vs. pool, nunca misturado) mantém "toda tabela de tenant tem `tenant_id` sempre" trivialmente revisável em code review — não há branch condicional por linha para auditar.
- **Positivo:** `tenant_credential` isolado permite rotação/revogação por provider sem tocar outras keys do tenant; changelog de auditoria (decisão do KUBO-104) se aplica à mesma tabela.
- **Trade-off:** enforcement só em `kubo/store/` depende de disciplina de code review — um bug ali é um vazamento entre tenants. RLS nativo do SurrealDB mitigaria, mas exige reprojetar o modelo de conexão; adiado.
- **Trade-off:** membership N:N é mais estrutura do que "1 usuário = 1 tenant fixo" pediria — mas foi decisão explícita do dono (suportar pessoa em tenant próprio + tenant de terceiros).
- **Neutro:** este ADR não define o fluxo de auth (KUBO-108), o mecanismo de dedup do pool (KUBO-107) nem o schema de catálogo por-tenant (KUBO-109) — só o chão comum que os três consomem.

## Alternativas rejeitadas

- **`tenant_id` anulável na mesma tabela** (`NULL` = pool, preenchido = tenant) — quebra a uniformidade do filtro; um dev esquecer o `OR tenant_id IS NULL` ou inverter a lógica vira vazamento entre tenants. Tabelas físicas separadas tornam o erro estruturalmente mais difícil.
- **NS/DB separado por tenant no SurrealDB** — isolamento mais forte fisicamente, mas complica o cenário de pool compartilhado (ficaria em qual NS/DB?) e não foi a direção escolhida no charting do KUBO-104.
- **RLS nativo do SurrealDB desde já** — exige trocar conexão root/`kubo_rw` por autenticação record-user; peça de arquitetura maior que este ADR não força agora.
- **1 usuário = 1 tenant fixo** — mais simples, mas o dono pediu explicitamente suporte a múltiplos tenants por pessoa.
- **Secret manager externo por tenant** — spec §5 já nega proxy de credenciais como peça pesada; criptografia em repouso no banco único resolve sem infra nova.
