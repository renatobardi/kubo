# Issue tracker: GitHub

Issues e PRDs deste repo vivem como GitHub Issues (`github.com/renatobardi/kubo`).
Use a `gh` CLI para todas as operações. O `gh` infere o repo do `git remote -v` quando
rodado dentro do clone.

## Governança: dois regimes (ADR-0024)

Princípio: **conteúdo durável vive no repo; issue nunca é registro permanente.**

- **Regime padrão (toda issue):** título + 1 parágrafo + link. Doc vivo = link de branch,
  nunca conteúdo inline. SHA-pin só para código que não se move.
- **Regime andaime (só `/wayfinder`):** o mapa (`wayfinder:map`) e seus tickets de decisão
  podem carregar conteúdo no corpo, porque são quadro-branco transitório com morte programada.
  Condições: (1) contexto pesado fora do corpo (gist/branch); (2) no handoff `/to-spec` o
  mapa morre — colapsa em spec/ADRs e é fechado linkando os artefatos, com descarte explícito
  nomeado de decisão não-colapsada; (3) mapa parado 30 dias ou aberto pós-handoff é violação;
  (4) o regime pertence ao ciclo de vida da skill, não ao label; (5) no máx. 1–2 mapas ativos.

Critério de decisão: *isto é registro ou andaime?* Ver ADR-0024 para o racional completo.

## Convenções

- **Criar issue**: `gh issue create --title "..." --body "..."`. Heredoc para corpos multi-linha.
- **Ler issue**: `gh issue view <number> --comments`, filtrando comentários com `jq` e buscando labels.
- **Listar issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` com filtros `--label` e `--state`.
- **Comentar**: `gh issue comment <number> --body "..."`
- **Aplicar / remover labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Fechar**: `gh issue close <number> --comment "..."`

## Pull requests como superfície de triagem

**PRs como superfície de pedido: não.** _(Mude para `sim` se este repo tratar PRs externos como
feature requests; `/triage` lê esta flag.)_

Quando `sim`, PRs passam pelos mesmos labels e estados das issues, via equivalentes `gh pr`
(`gh pr view/diff/list/comment/edit/close`). GitHub compartilha um espaço de números entre
issues e PRs, então um `#42` pode ser qualquer um — resolva com `gh pr view 42` e caia para
`gh issue view 42`.

## Quando uma skill diz "publish to the issue tracker"

Crie uma GitHub Issue.

## Quando uma skill diz "fetch the relevant ticket"

Rode `gh issue view <number> --comments`.

## Operações de wayfinding

Usadas por `/wayfinder`. O **mapa** é uma issue única com issues **filhas** como tickets.
Regidas pelo regime andaime acima (ADR-0024).

- **Mapa**: issue com label `wayfinder:map`, contendo o corpo Notes / Decisions-so-far / Fog.
  `gh issue create --label wayfinder:map`.
- **Ticket filho**: issue ligada ao mapa como sub-issue do GitHub (`gh api` no endpoint de
  sub-issues). Onde sub-issues não estão habilitadas, adicione o filho a uma task list no corpo
  do mapa e ponha `Part of #<map>` no topo do corpo do filho. Labels: `wayfinder:<type>`
  (`research`/`prototype`/`grilling`/`task`). Ao ser reivindicado, o ticket é atribuído ao dev.
- **Bloqueio**: **issue dependencies nativas** do GitHub (representação canônica, visível na UI).
  Adicione aresta com `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`,
  onde `<blocker-db-id>` é o **database id** numérico do bloqueador
  (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _não_ o `#number` nem o `node_id`).
  Fallback: linha `Blocked by: #<n>, #<n>` no topo do corpo do filho. Ticket desbloqueia quando
  todo bloqueador está fechado.
- **Frontier query**: liste os filhos abertos do mapa, descarte os com bloqueador aberto
  (`issue_dependencies_summary.blocked_by > 0`) ou com assignee; primeiro na ordem do mapa vence.
- **Claim**: `gh issue edit <n> --add-assignee @me` — a primeira escrita da sessão.
- **Resolver**: `gh issue comment <n> --body "<answer>"`, depois `gh issue close <n>`, depois
  anexe um ponteiro de contexto (gist + link) ao Decisions-so-far do mapa.
