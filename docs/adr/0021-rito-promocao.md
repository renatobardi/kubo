# ADR-0021 — Rito de promoção: worker → grafo (deploy-gap, import-oráculo, gate sequencial)

> Status: **proposto** (DRAFT p/ validação do advisor Fable 5 antes do marco 18.11)
> Data: 2026-07-15 (sessão 0018, parte A)
> Estende ADR-0009 (contrato de worker), ADR-0016 (spec §3.1 fabricação), ADR-0018 (gate humano), ADR-0019 (executor cli).
>
> **Notas de execução:** as 10 decisões abaixo foram validadas pelo advisor Fable 5 no planejamento da 0018. Este ADR registra cada uma com seu racional. Crave somente após aprovação do advisor no checkpoint 18.11.

## Contexto

A spec §3.4 descreve o rito de promoção: "worker sob contrato chega por PR → validação e registro → instanciação em pipeline operacional". Implementado literalmente: merge do PR dispara registro automático do worker no grafo.

Tensão estrutural: **workers são código Python** — precisam estar **compilados na imagem Docker**. Não são arquivos YAML. Então "merge dispara registro" choca com a realidade operacional: o worker só resolve (`from kubo.workers import github_releases`) DEPOIS que o container rebuilda e sobe. Deploy não é instantâneo; é etapa explícita.

**Desvio consciente da spec:** o rito fica com **quatro passos sequenciais**:
1. PR chega (gate na UI, mercancia do ADR-0018).
2. Dono mescla (GitHub, não Kubo).
3. `./scripts/deploy.sh` (container novo sobe; o worker agora resolve).
4. Botão **"Confirmar promoção"** (UI valida via API do GitHub + import no processo vivo; worker promovido no grafo).

O passo 4 é o **novo**: a spec não o prevê porque assume workers declarativos (YAML). Aqui, é a porta de sincronização entre código Python vivo e o grafo de conhecimento. Sem poll, sem webhook, sem carga dinâmica — justamente as tres técnicas que o escopo negativo proíbe.

## Decisão

### I. Deploy-gap: o passo explícito de confirmação (D41, E10, E14)

**Motivo:** worker Python não resolve até a imagem estar viva. "Merge dispara" virou "dono confirma pós-deploy (e o processo valida que o worker está lá)".

**Por que não poll ou webhook:** poll torna o rito tempo-dependente (quanto tempo esperar?); webhook exige o Kubo anunciar-se externamente (Tailscale-only, nada de egress). Nem precisa — o dono **está** clicando "Confirmar promoção" no browser. Esse clique é o gatilho natural.

**Oráculo = processo vivo:** confirmar faz (a) validar merge via GitHub API read-only (`merged: true`, gravar `merge_commit_sha`); (b) validar que `worker_name ∈ WORKER_REGISTRY` + manifest resolvido **no próprio processo da API** (em memória, sem subprocess). Se resolver, a imagem contém o merge por construção. Se não resolver → erro estruturado legível ("worker não está na imagem; rode ./scripts/deploy.sh") → gate aberto, dono reclica. Sem surpresas.

**Edge nomeado (não resolvido agora):** ancestralidade — PR novo vs merge velho no registry. Gravando `merge_commit_sha`, o operador único (dono) consegue auditar 6 meses depois ("confirmei X, SHA foi Y").

### II. Worker name owner-supplied (E10, E14)

Dev flow não captura `worker_name` estruturalmente — é instrução freeform no Kubo. Após merge e deploy, o dono **clica em "Confirmar promoção"** e informa o nome do worker na GateSheet da promoção (input **trusted** — o dono cria tasks; não misturar terceiros aqui).

O processo valida contra o próprio registry (self-validation): `worker_name in WORKER_REGISTRY` resolve ou levanta. Laziest correto — sem campo novo no schema, sem migration.

**Limitação residual nomeada (achado do advisor, validado antes do smoke):** o oráculo prova "este nome resolve na imagem viva", **não** "este PR introduziu este worker". O dono pode digitar por engano o nome de um worker JÁ existente (ex.: `feed`) num flow de OUTRO worker, e a confirmação "passa" mesmo assim — `merge_commit_sha`+`worker_name` no grafo não têm vínculo estrutural verificado entre si. Mitigação aceita para operador único: auditoria manual via `merge_commit_sha` gravado no deliverable (o dono confere, se precisar, que o SHA do merge corresponde ao worker nomeado). Não resolvido nesta sessão; se a promoção virar rotina de mais de um operador, este é o primeiro ponto a endurecer.

### III. Registro = código + relocação (E9, ADR-0010 intacto)

Hoje, `WORKER_REGISTRY` vive em `kubo/scheduler/__init__.py`. **Move para `kubo/workers/registry.py`** (só o dict de workers; scheduler importa de lá, API importa sem puxar APScheduler — ganho de camadas).

PR de worker = arquivo `kubo/workers/x.py` + 1 linha no registry + testes (contido pelo path-guard CI da 0018b). Continua **dict hardcoded** — ADR-0010 intacto, nada de carga dinâmica.

**Recusa explícita:** `catalogs/workers/` (seria 4ª categoria de catálogos — violaria invariante 3 sem decisão explícita do dono).

### IV. Gate sequencial: `done` deixa de ser terminal em dev-mini v2 (E11, ADR-0016 §I)

Spec §3.1.3 manda o rito de promoção ser **gate declarado no template** (`done → promoted`), não regra hardcoded no runtime.

**Emenda ao dev-mini:** board ganha estado `promoted`. Gate `[done, promoted]` no template (approve-only; "rejeitar" não faz sentido num merge, o card parado em `done` = "aguardando deploy + confirmação").

**Proteção de snapshot:** flows dev-mini v1 (legados, sem `promoted`) protegidos por invariante 4 — snapshot congela o template na instanciação. Novos flows rodando dev-mini v2; os antigos, v1.

**Custo de generalização (E11):** maquinaria do ADR-0019 §XII (gate sequencial) precisa generalizar para suportar os DOIS templates. Terminal-ness derivada do snapshot, nunca de literais (vide seção VI abaixo). Consulta extraordinária ao advisor se >~3h (alternativa: flow separado com emenda à spec — rejeitado com aval do advisor antes de implementar).

### V. Terminal-ness derivada do snapshot, não literais (E11, ADR-0019 §XII)

Definição: **um estado sem transição de saída no snapshot é terminal.**

Motivo: literais (`done_terminal = true` no YAML) **não conseguem servir v1 (`done` terminal) e v2 (`done` com saída para `promoted`) ao mesmo tempo** — há flows v1 vivos no banco. Derivação resolve os dois de graça; é propriedade estrutural, não declaração.

Convenção: terminais-de-falha = `{rejected, failed}` (nomes reservados); terminais-de-sucesso = terminais − falha.

**Impacto:** `status/tasks_open/detecção-de-gate` derivam de snapshot via `terminal_no_snapshot`. Sem esta regra, flow v2 parado em `done` (não-terminal) apareceria "rodando" eternamente.

### VI. `closed(task) := terminal_no_snapshot ∨ decision_presente` (E11, ADR-0019 §XII)

Task humana de gate (ex.: `review` no analysis) **DECIDIDA** fica estacionada em `done` (não-terminal em dev-mini v2) — sem esta regra, flow apareceria sem gate de promoção aberto.

Discriminador: campo `decision` na task (único lugar onde mora; gravado na **mesma transação** do `decide_gate`, nunca em risco de dessincronizar).

Ajuda único helper de detecção, três leitores (status, tasks_open, gate-aberto). Honesto e composável.

### VII. Auto-open atômico do próximo gate (E11)

Aprovar `review → done` cria a **PRÓXIMA task humana** (`review → promoted`) em `done`, tudo **numa transação única**. Mecanismo: `CREATE` condicional (`IF array::len($moved) > 0`) impede corrida double-decide.

Persona derivada do próprio gate task (`->assigned_to->`, a pessoa que aprovou; sem parâmetro novo).

**Restrição nomeada:** gates são decisões humanas **ENTRE estados**. Um worker rodando entre dois gates exigiria estado intermediário no board (não suportado; regra de design).

### VIII. Terceira porta de escrita da UI: `/flows/gate/promote` (E12, emenda ao ADR-0018 D38)

"Confirmar promoção" é rota **própria** (`POST /flows/<id>/gate/promote`), não sobrecarrega o aprovador genérico (`decide_gate`). Motivo: **validações de segurança (merged + registry) não ficam escondidas** atrás de um `if`.

Token de **leitura no confirmar** (E12): chamada ao GitHub (`/repos/.../pulls/<n>`, check merged) usa **PAT read-only** (novo: `GITHUB_TOKEN_READONLY`, integração `github-readonly.yaml`). **NUNCA** o PAT de escrita do sandbox.

**Antecipação de escopo nomeada (achado do advisor):** o plano original agendava o plumbing do token read-only (E13) para a parte B (marco 18.7). Como a parte A (18.4/18.6) já CONSOME esse token no Confirmar, o wiring (integração + env no compose + `.env.example`) entrou aqui, na parte A — dependência dura descoberta em execução, registrada em vez de replanejar. O YAML `github-releases.yaml` do coletor (marco 18.9) reusa o MESMO env `GITHUB_TOKEN_READONLY`.

**Ordem I/O-antes-de-commit:** valida API + registry ANTES do `decide_gate`; falha estruturada → gate aberto, dono reclica. Espelha `_reject_dev` (ADR-0019 §XII).

### IX. Loop de auto-amplificação: código mesclado é contexto (E5)

Código de agente mesclado **vira contexto** dos próximos agentes (fase 4: agente lê repo, vê trabalho anterior, estende).

**Superfície de segurança:** comentários, docstrings e strings de PR de agente são **texto de terceiros** (agente é máquina, mas o prompt que o orienta pode vir de contexto coletado na fase 4). Review humano do dono + CodeRabbit trata essa superfície como **hostil por padrão** (disciplina existente de conteúdo coletado: sanitização antes de prompt).

**Nomear no ADR:** a qualidade da revisão do dono é **componente de segurança**, não só de qualidade.

### X. Promoção é ambiente-local por construção (E14)

Propriedade, não acidente: o processo valida a si mesmo (registry local, import em processo, sem rede). Card parado em `done` com gate aberto deve ser legível no board ("aguardando deploy + confirmação"); senão, em 6 meses parece bug.

**Operacionalmente:** depois que deploy subir, sempre é seguro clicar "Confirmar promoção" de novo — idempotência via `merged_commit_sha` já gravado no grafo.

**Nota sobre o smoke da parte A (18.6, achado do advisor pré-smoke):** `./scripts/deploy.sh` deploya a WORKING TREE do Mac (rsync, não `git HEAD` — comentário do próprio script), não o repo `kubo-forge` (sandbox onde o `dev-mini` abre PR). Logo o worker que o Confirmar promove PRECISA estar no repo `kubo` local, não no PR do sandbox — os dois são repos DISTINTOS. Para o smoke da parte A, a cerimônia fica **conscientemente encenada**: o PR/gate/merge no `kubo-forge` prova a MECÂNICA da UI (review→gate→GateSheet); o worker confirmado usa um nome JÁ registrado (`feed`) em vez de um worker novo — evita a dança de escrever+registrar+re-deployar código só para o smoke. O primeiro worker gerado de fato pelo agente, com PR no repo CERTO (`renatobardi/kubo`, D41), é o smoke da parte B (18.10, `github_releases`) — ali a cerimônia deixa de ser encenada.

## Consequências

- **Positivo:** promoção é mecânica provada ponta a ponta; gate humano segue **por construção** (D38, D42); worker code+registry localizados (`kubo/workers/`); dev-mini v2 generaliza gate sequencial (reutilizável em outras transições futuras).
- **Operacional:** dono executa 4 passos sequenciais (merge no GitHub + deploy + clique "Confirmar"); custo de automação evitado por design (Tailscale-only, sem poll/webhook). Botão "Confirmar promoção" é ato deliberado, rastreável.
- **Trade-off:** worker_name é input manual do dono (sem estrutura capturada no agente); mitiga com validação em processo + audit com `merge_commit_sha`. Segurança de revisão humana de agente dependente da qualidade do review do dono (interface de segurança explícita).
- **Segurança:** import-oráculo elimina o gap "merge mas worker não está" (erro legível, gate aberto); read-only PAT no confirmar reduz blast radius de credenciais de escrita (segunda porta de escrita da UI, terceira contida); relocação do registry limpa a camada (scheduler não puxa APScheduler via API).

## Alternativas rejeitadas

- **Poll de merge status** — tempo-dependente, sem limite claro de retry. Rejeitada.
- **Webhook GitHubHost** — exige Kubo anunciar-se externamente; Tailscale-only, sem egress. Rejeitada.
- **Carga dinâmica de código** (importlib) — violaria invariante 1 (Python é runtime) e criaria superfície de injection ao carregar `.py` do grafo. Rejeitada.
- **Worker name capturado estruturalmente pelo agente** — dev flow não tem estrutura para isso; field novo no template = mudança de schema do dev-mini sem evidência de uso. Laziest correto é input manual trusted. Rejeitada.
- **Terminal-ness por literal no YAML** — não consegue servir v1 e v2 ao mesmo tempo. Rejeitada.
- **Dois leitores de gate (genérico + dev-específico)** — duplica lógica de segurança (anti-forja); snapshot é única fonte (invariante 4). Rejeitada.
- **PAT de escrita no confirmar** — blast radius desnecessário; read-only basta para validação. Rejeitada.

---

**Próxima etapa:** checkpoint 18.11 — advisor valida cada decisão; thread principal crava versão final após aprovação e integra à sessão de execução.
