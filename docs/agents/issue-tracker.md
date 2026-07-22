# Issue tracker: Jira

Issues e PRDs deste projeto vivem como **issues do Jira**, no projeto **`KUBO`** da instância
`oute.atlassian.net`. As operações passam pelo **MCP do Atlassian (Rovo)** — ferramentas
`mcp__claude_ai_Atlassian_Rovo__*`. Não há CLI dedicada; as chamadas são via MCP.

> **Histórico:** até 2026-07-19 o tracker era GitHub Issues (`github.com/renatobardi/kubo`).
> As 33 issues abertas foram migradas para `KUBO-1..33` e o GitHub foi fechado. O repositório Git
> segue no GitHub (código, PRs, ADRs); só o **tracking de tickets** mudou para o Jira. Ver
> ADR-0026 para a governança, que é agnóstica de tracker (o tracker é config, não regra).

## Coordenadas (fixas)

- **cloudId**: `77dbeb01-8de4-4ad9-8895-ebc2a711f18c` (ou passe `oute.atlassian.net` como cloudId).
- **projectKey**: `KUBO` (Kanban, team-managed).
- **Tipos de item** (o nome é **localizado em PT** — use exatamente):
  `Epic`, `Tarefa`, `História`, `Função`, `Bug`, `Subtarefa`.
  ⚠️ `createJiraIssue` exige `issueTypeName="Tarefa"` — **`Task` falha** ("Especifique algum tipo
  de item válido").
- **Descobrir/testar acesso**: `atlassianUserInfo` → `getAccessibleAtlassianResources` (dá o
  cloudId) → `getVisibleJiraProjects`. Se os tools `mcp__claude_ai_Atlassian_Rovo__*` não
  aparecerem (ToolSearch `atlassian`/`jira`), o MCP não subiu — **pare e avise**, não caia para
  outro tracker.

## Governança (ADR-0026, substitui ADR-0024)

**Issues podem carregar conteúdo à vontade** — specs/PRDs (`/to-spec`), mapas de wayfinder,
notas de design. Não há regime de "issue-ponteiro". Único guardrail:

> **Cópia congelada nunca é canônica.**
> (a) Link para doc vivo = link de **branch**; SHA-pin só para código imóvel.
> (b) Corpo de issue que **duplica** artefato do repo é snapshot — **o repo é canônico**; em
> divergência, o corpo perde, sem discussão.

Ver ADR-0026 para o racional. (A cláusula "o mapa morre no handoff" é agora **prática da skill
`/wayfinder`**, não regra do repo.)

## Convenções

Todas as chamadas levam `cloudId` e, quando criam/leem no projeto, `projectKey="KUBO"`.

- **Criar issue**: `createJiraIssue` com `projectKey="KUBO"`, `issueTypeName="Tarefa"`,
  `summary`, `description` (Markdown por default). Labels e campos extras vão em
  `additional_fields`, ex.: `{"labels": ["backlog"]}` (labels Jira aceitam hífen, **não** espaço).
- **Ler issue**: `getJiraIssue` com `issueIdOrKey="KUBO-<n>"`. Comentários e histórico via os
  campos expandidos do próprio tool.
- **Listar / buscar issues**: `searchJiraIssuesUsingJql`, ex.:
  `project = KUBO AND statusCategory != Done ORDER BY key ASC`. Filtre por `labels`, `assignee`,
  `status`. ⚠️ Uma query de projeto inteiro pode estourar o limite de tokens do resultado — peça
  só os `fields` necessários (`["key","summary","labels","status"]`) e pagine com `maxResults`.
- **Comentar**: `addCommentToJiraIssue` com `issueIdOrKey` e `commentBody`.
- **Aplicar / remover labels e campos**: `editJiraIssue` (passe `labels` em `fields` /
  `additional_fields`).
- **Fechar / mover de estado**: transição de workflow, não edição de campo.
  `getTransitionsForJiraIssue` para achar o id da transição (ex.: "Concluído") e
  `transitionJiraIssue` para aplicá-la. Deixe o comentário de fecho via `addCommentToJiraIssue`.

## Ciclo de vida de trabalho (sessões de agente)

O workflow do KUBO é **sequencial, sem atalhos**: transicionar Backlog → Running direto **falha na
API** — cada status só alcança o vizinho. Mapa verificado empiricamente em 2026-07-22
(nome da transição e id entre parênteses):

```text
Backlog ─Priorizado (2)→ Tarefas pendentes ─Iniciado (3)→ Running ─Homologar (4)→ Validate ─Entregue (5)→ Concluído
```

Retornos: *Despriorizado* (6): Tarefas pendentes → Backlog; *À corrigir* (7): Validate → Running.
`Concluído` é **terminal** (zero transições de saída).
Os ids podem mudar se o dono editar o workflow: em erro de transição, **não desista em silêncio** —
rode `getTransitionsForJiraIssue` e siga hop a hop até o status alvo.

**Ao iniciar trabalho num ticket** (primeira escrita da sessão):

1. Claim: `editJiraIssue` com `assignee` (accountId via `atlassianUserInfo`).
2. Transicionar até **Running**, hop a hop (de Backlog: *Priorizado* → *Iniciado*).
3. Label: adicionar `running` **preservando as demais labels** (`needs-*`, `wayfinder:*`...).
   Preferir o `update` da própria `transitionJiraIssue`
   (`{"labels": [{"add": "running"}]}` — add/remove, nunca `set`). Via `editJiraIssue` não há
   add/remove: `fields.labels` **substitui o array inteiro** — leia as labels atuais
   (`getJiraIssue`) e regrave a lista completa com a troca feita.

**Ao terminar o trabalho da sessão:**

1. Transicionar para **Validate** (*Homologar*), com `update`
   `{"labels": [{"add": "validate"}, {"remove": "running"}]}`.

Nunca deixe o ticket no status em que a sessão o encontrou — com duas exceções:

- **Ticket já em `Concluído`**: terminal — não faça claim nem transição; se precisar de retrabalho,
  quem reabre é o dono, pela UI.
- **Ticket de decisão do `/wayfinder`**: segue a prática da skill (resolver → `Concluído` direto,
  ver §Operações de wayfinding), sem passar por Running/Validate.

Fora essas exceções, quem move para `Concluído` é o dono, após validar.

## Pull requests como superfície de triagem

**PRs como superfície de pedido: não.** _(Mude para `sim` se este projeto tratar PRs externos
como feature requests; `/triage` lê esta flag.)_

PRs continuam no **GitHub** (`gh pr ...`) — são artefato de código, não de tracking. Se algum dia
`sim`, um PR relevante vira uma issue `KUBO` correspondente via `createJiraIssue`, com o link do
PR no corpo; a issue Jira é que carrega labels e estado de triagem.

## Quando uma skill diz "publish to the issue tracker"

Crie uma issue no Jira: `createJiraIssue` (`projectKey="KUBO"`, `issueTypeName="Tarefa"`).

## Quando uma skill diz "fetch the relevant ticket"

Rode `getJiraIssue` com a chave `KUBO-<n>` (ou `searchJiraIssuesUsingJql` se só houver o texto).

## Operações de wayfinding

Usadas por `/wayfinder`. O **mapa** é um **Epic**; os tickets são issues **filhas** do épico.
A cláusula de handoff e os detalhes abaixo são **práticas da skill `/wayfinder`**, não um regime
de governança do repo (ver Governança / ADR-0026).

- **Mapa**: `createJiraIssue` com `issueTypeName="Epic"` + label `wayfinder:map`, contendo o corpo
  Notes / Decisions-so-far / Fog.
- **Ticket filho**: `createJiraIssue` com `issueTypeName="Tarefa"` e `parent="KUBO-<epic>"`
  (hierarquia nativa Epic → Tarefa no team-managed). Label `wayfinder:<type>`
  (`research`/`prototype`/`grilling`/`task`). Ao ser reivindicado, atribua ao dev (`editJiraIssue`
  com `assignee`).
- **Bloqueio**: **issue link** nativo do Jira. `getIssueLinkTypes` para achar o par
  "Blocks / is blocked by"; `createIssueLink` ligando bloqueado ← bloqueador. Visível na UI.
- **Frontier query**: `searchJiraIssuesUsingJql`, ex.:
  `parent = KUBO-<epic> AND statusCategory != Done AND assignee IS EMPTY ORDER BY rank ASC`,
  depois descarte em código os que têm link "is blocked by" com bloqueador aberto; primeiro na
  ordem do épico vence.
- **Claim**: `editJiraIssue` atribuindo a si (`assignee` = seu accountId, de `atlassianUserInfo`
  ou `lookupJiraAccountId`) — a primeira escrita da sessão.
- **Resolver**: `addCommentToJiraIssue` com a resposta, depois `transitionJiraIssue` para
  "Concluído", depois anexe um ponteiro de contexto (gist + link) ao Decisions-so-far do épico
  (`editJiraIssue` no corpo do épico).
