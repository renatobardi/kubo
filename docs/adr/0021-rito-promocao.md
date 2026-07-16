# ADR-0021 — Rito de promoção: worker → grafo (deploy-gap, import-oráculo, gate sequencial)

> Status: **aceito** (cravado no checkpoint 18.11, 2026-07-15, após validação do advisor Fable 5)
> Data: 2026-07-15 (sessão 0018, partes A e B)
> Estende ADR-0009 (contrato de worker), ADR-0016 (spec §3.1 fabricação), ADR-0018 (gate humano), ADR-0019 (executor cli).
>
> **Notas de execução:** as 10 decisões I-X foram validadas pelo advisor Fable 5 no planejamento da 0018 (parte A) e provadas fisicamente no smoke da parte A (18.6). As seções XI-XIII são achados do smoke físico da parte B (18.10, fallback D44) e passaram por rodada própria de validação do advisor no checkpoint 18.11 antes de cravar.

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

## Achados do smoke físico (18.10, D44 fallback — parte B)

O smoke da parte B rodou o `dev-kubo` de verdade contra `renatobardi/kubo` (D41): PR #49
(`agent/s7v7s5wgb3wr2e64afrc`, US$1.2685, 30 turnos) tropeçou uma vez (bug de type-narrowing
pyright no próprio teste do agente) — rejeitado pela UI com `GITHUB_PAT_KUBO`, provando o
caminho de reject no repo PRINCIPAL, não só no sandbox `kubo-forge` (D44: tropeço 1 → CLI
assume a escrita). CLI reescreveu (reaproveitando ~integralmente o código do agente, que já
batia com o enunciado), abriu PR #50 (`feat/github-releases-worker`), mesclado após revisão do
dono. Três achados deste ciclo entram no ADR — não os achados TÉCNICOS do worker em si
(403/rate-limit, cobertura do streaming, nit do validador, backfill, `published_at`), que
ficam registrados em `docs/sessions/fase4-roadmap.md` D51 (dívida de produto/worker, fora do
escopo desta ADR de RITO).

### XI. Gate automático + revisor-LLM-como-serviço são NECESSÁRIOS, NÃO SUFICIENTES (achado central)

**Os fatos** (checados contra check-runs/reviews reais, não memória — ver "Nota de método"
abaixo): no PR #49 (`agent/s7v7s5wgb3wr2e64afrc`), `quality` (pyright) **FALHOU**, pegando um
bug de type-narrowing no teste do próprio agente (`.payloads[0].external_id` sem `isinstance`)
— MECÂNICO, sem relação com segurança. O CodeRabbit **NUNCA rodou** no #49 (rate limit da
plataforma — o check-run reporta `success` porque é só o bot confirmando que TENTOU, não
evidência de revisão real). Dos automatismos que de fato EXAMINARAM o código (ruff, pyright,
tests, integration, SonarCloud), **nenhum é desenhado para pegar sanitização de campo
ausente** — não é a classe de bug que essas ferramentas endereçam (`quality` pegou UM bug real,
só que o bug errado para este achado). Mesmo assim, o passe adversarial dedicado
(`security-reviewer`, thread principal invocando fora do circuito de CI, sobre o MESMO código
do #49 — reaproveitado ~integralmente no #50 antes de qualquer correção) achou um **ALTO**
real: `tag_name` gravado cru em `metadata` — um surrogate solto sobreviveria até o encoder CBOR
estrito do SDK SurrealDB (ADR-0005) e abortaria a persistência do **batch inteiro** (não só o
item), o oposto da falha-parcial que o próprio docstring do worker promete (ADR-0009 §VII). A
evidência fica ancorada no #49: o #50 não atesta nada sobre o ALTO — já estava corrigido
quando o CodeRabbit finalmente rodou (pela primeira vez neste ciclo, sem rate limit), achando
**3 itens diferentes** (403≠rate-limit, teste de streaming, nit de validador — D51), nenhum
deles o ALTO.

**Duas afirmações distintas, duas cargas de prova distintas:**

1. **"O que de fato rodou sobre o #49 não foi suficiente para pegar um bug de segurança
   real"** — afirmação de INSUFICIÊNCIA, provada por observação direta (não amostragem): dos
   mecanismos automáticos que EXAMINARAM o código, nenhum é do tipo que endereça esta classe de
   bug; o mecanismo que poderia (CodeRabbit) não rodou por falha operacional real. N=1 basta —
   a questão fica encerrada pela observação, não por estatística.
2. **"Security-reviewer é peça estrutural do rito"** — isto é PRESCRIÇÃO, e N=1 não prova
   prescrição sozinho. A prescrição se sustenta por custo assimétrico: um passe de subagent por
   promoção é barato; um batch-abort silencioso na persistência — ou pior, na fase 4, com
   conteúdo hostil de terceiro já fluindo pelo sistema — é caro. A amostra prova que a
   alternativa mais barata (CI + revisor-serviço genérico) falha; o custo assimétrico é o
   argumento que justifica pagar pela mais cara.

**Por que o passe achou o que o CodeRabbit não achou (o mecanismo, não só o resultado):** o
passe dedicado carregava CONTEXTO DE INVARIANTE DO PROJETO — a promessa de falha-parcial do
ADR-0009 §VII e a estritude CBOR do SDK SurrealDB pinado (ADR-0005) — que um revisor-serviço
genérico não tem por que conhecer. O requisito estrutural correto não é "rode um segundo
revisor LLM qualquer" (que falharia pelo mesmo motivo do CodeRabbit); é **passe adversarial COM
os invariantes do projeto no contexto** — o mesmo padrão que já rege `security-reviewer` em
`kubo/store/`, `kubo/contracts/`, `kubo/executors/`, `kubo/workers/` no CLAUDE.md.

**Segunda insuficiência — de DISPONIBILIDADE, não de acerto.** O CodeRabbit NUNCA rodou no #49
(rate limit da org, "couldn't start this review"). O revisor-serviço esteve ausente exatamente
no PR que continha o ALTO: não há evidência de que o teria achado, nem de que não. O que se
prova aqui não é cegueira dele — é que **a disponibilidade dele não é contável**. Um gate que
pode silenciosamente não rodar não sustenta uma decisão de promoção; falha ABERTO. Quando rodou
(no #50, código já corrigido), achou 3 itens acionáveis reais — tem valor quando roda; o
problema é que "quando" não é garantia. Isto não substitui o argumento de custo assimétrico da
prescrição, soma com uma alternativa: "é só confiar no revisor-serviço" não é opção, porque ele
não está sempre lá.

**Nota de método — achado sobre o PRÓPRIO ADR (registrado por ser o mais barato de esquecer):**
a primeira redação desta seção afirmava que o #49 passou todos os gates automáticos e que o
CodeRabbit o revisou limpo. As DUAS coisas eram falsas: o `quality`/pyright FALHOU no #49 (foi o
motivo do reject) e o CodeRabbit não rodou. O erro foi escrito de memória e só caiu quando se
foi buscar os check-runs do commit. É o E3 do ADR-0019 ("estrutura nunca vem do texto do agente
— URL, SHA e todo dado estrutural vêm da resposta da API") aplicado ao próprio documento:
**resultado de gate é dado estrutural. Vem do check-run, nunca da lembrança de quem escreve o
ADR.**

**Endereço operacional (o slot, não só o mandato):** no caminho de EXCEÇÃO (D44, este smoke), o
passe rodou dentro do fallback, antes de reabrir o PR. No caminho NORMAL (PR de agente sem
tropeço), o slot é: o passe roda sobre o diff do PR do agente **ANTES da decisão do gate do
dono na UI**, e seus achados são insumo dessa decisão — mecânica de disparo (automático no CI?
manual pela thread principal antes de notificar o dono?) fica como dívida nomeada no roadmap,
mas o SLOT (quando, sobre o quê, alimentando qual decisão) já está fixado aqui.

Isto ESTENDE E CONFIRMA EMPIRICAMENTE o §IX (E5, "a qualidade da revisão do dono é componente de
segurança, não só de qualidade") desta mesma ADR: revisão automatizada (CI + CodeRabbit) sozinha
não basta; precisa da camada adversarial dedicada antes do merge, não como rede pós-merge.

**Reavaliação futura:** se nas próximas 2-3 promoções de worker o passe não produzir achado que
sobreviva a triagem, "peça estrutural" pode ser rebaixado a "amostragem periódica" — registrar
os resultados dos próximos passes para essa reavaliação ser possível.

### XII. Corolário: não se pode CONTAR que o agente generalize princípio a partir da letra do enunciado

O 403-como-rate-limit (D51 #1) não é um bug que o agente inventou — o enunciado da task
(craveado pela thread principal no 18.9) dizia literalmente "rate limiting: on 403/429...";
o agente seguiu a LETRA. Mesmo padrão do lado bom: o agente tratou `body`/`name` do release
como markdown hostil porque o enunciado mandou explicitamente — mas deixou `tag_name` cru
porque o enunciado enumerou campos a limpar sem incluí-lo por nome.

Dois data points correlacionados (mesmo enunciado, mesma sessão) NÃO estabelecem uma lei geral
de comportamento de agente — LLMs às vezes generalizam corretamente, às vezes generalizam
demais (inventam). A afirmação correta não é comportamental-universal; é de CONFIABILIDADE:
**não se pode contar que o agente generalize o princípio de segurança sozinho**, e para essa
afirmação mais fraca, N=1 basta (mesmo raciocínio de contraexemplo da XI). A implicação prática
fica de pé independente de o agente às vezes generalizar: **spec de segurança não pode depender
de generalização espontânea.**

**A responsabilidade pela completude do enunciado é de quem o escreve.** A disciplina "cravar
TUDO, senão o agente inventa" (plano 0018, marco 18.9) fica validada empiricamente por este
achado — mas na direção inversa da intuição: aqui o problema não foi o agente inventar, foi o
enunciado sub-especificar um campo dentro de uma lista fechada. Implicação prática para specs
futuras de worker: enumerar o PRINCÍPIO de segurança ("todo campo string vindo da API de
terceiro passa por `_clean`", não uma lista fechada de nomes de campo) é mais robusto contra
esta classe de gap — e tem um bônus barato: princípio enumerado é VERIFICÁVEL pelo passe da XI
("todo campo string de terceiro passa por `_clean`?" é pergunta checável mecanicamente); lista
fechada de campos não é. As duas seções se reforçam.

### XIII. Lacunas e decisões em aberto, nomeadas (não resolvidas agora)

- **Decisão em aberto (não é dívida, e não pré-comprometer): quem deve escrever o catálogo de
  integração — dono ou agente?** `catalogs/integrations/github-releases.yaml` já estava em
  `main` antes do disparo do agente — colocado pela thread principal no 18.9 como **escolha
  consciente de escopo da sessão**, não omissão. O caminho "PR de agente autossuficiente
  incluindo catálogo" fica sem evidência de smoke — mas VERIFICADO: não está bloqueado (o
  allowlist do `agent-path-guard` inclui `catalogs/` inteiro, `.github/workflows/ci.yml:99`),
  então é lacuna de COBERTURA de smoke, não lacuna estrutural. Mais: há uma pergunta de
  desenho por trás da lacuna de cobertura, que este ADR NÃO resolve agora — `catalogs/
  integrations/` declara a fronteira de least-privilege do projeto; "humano declara a
  superfície de integração, agente escreve código dentro dela" é postura de segurança
  defensável, possivelmente MELHOR que o PR autossuficiente. Duas opções nomeadas para decisão
  futura: (a) PR de agente pode incluir `catalogs/integrations/` quando o worker precisa de
  integração nova; (b) catálogo de integração é sempre pré-colocado pelo dono/thread principal,
  o agente só recebe a integração já declarada. Resolve com evidência de smoke futuro +
  preferência explícita do dono — não antes.
- **Allowlist do `agent-path-guard` é mais larga do que o necessário.** `catalogs/` inteiro
  está permitido para PR de agente — incluindo `catalogs/personas/` (o mecanismo de
  least-privilege do projeto: permissões de integração por persona) e `catalogs/
  flow_templates/`. Um PR de agente editando permissões de uma persona passa no path-guard
  hoje. O gate humano na UI ainda vê o diff antes de aprovar — mas o guard existe como DEFESA
  EM PROFUNDIDADE, e apertar o allowlist para `catalogs/integrations/` especificamente é uma
  linha de CI. Não bloqueia cravar este ADR (é mudança de CI, não de arquitetura) — nomeado
  aqui e como item no roadmap para não ficar esquecido.
- **`agent-path-guard ✅` no PR #50 é VÁCUO — não conta como evidência do guard.** #50 é
  `feat/github-releases-worker` (autoria CLI/dono, D44), não `agent/*`; a lógica do guard
  (18.8) sai `exit 0` cedo pra qualquer branch fora de `agent/*` — desenho DELIBERADO (um
  required check cujo JOB é pulado via `if:` fica pending pra sempre; a condição vive DENTRO
  do step para que PRs humanos sempre reportem sucesso). A evidência REAL de que o path-guard
  funciona é o **PR #49** (`agent/s7v7s5wgb3wr2e64afrc`), que passou a checagem de allowlist
  legitimamente contra um diff de verdade. Auditorias futuras do rito devem citar #49, não #50,
  como prova do guard.
- **Proveniência do código de agente sob fallback D44 fica fora da trilha `agent/*`.** O #50 é
  código ~100% de autoria de agente (reaproveitado do #49) que entrou em `main` por um branch
  `feat/*` comum — fora do path-guard E fora de qualquer marcador estrutural de "isto veio de
  um agente". Uma auditoria futura que confie só em "todo código de agente passou por
  `agent/*` + guard" vai **silenciosamente não ver** PRs assim. Mitigação já aplicada neste
  smoke (conferido, não é tarefa pendente): a descrição do PR #50 declara a proveniência em
  texto ("D44 fallback: the `dev-kubo` agent run (PR #49)..."). Regra nomeada daqui pra frente:
  **todo PR de fallback D44 DEVE declarar a origem (número do run/PR do agente) no corpo do
  PR** — texto livre, não campo estrutural, mas obrigatório por disciplina.

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

**Checkpoint 18.11 fechado (2026-07-15):** advisor validou as seções XI-XIII (achados do smoke
físico, uma rodada de correções aplicada — ver histórico do PR); status cravado como aceito.
Dívidas nomeadas nas seções XIII e nos itens correspondentes de `docs/sessions/
fase4-roadmap.md` (Fila) ficam para sessão futura — nomeadas, não resolvidas aqui.
