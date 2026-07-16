# Fase 4 — Roadmap: auto-extensão via AI-DLC

> **Status:** aprovado pelo dono (2026-07-15, planejamento no Cowork); advisor GO com emendas incorporadas
> **O que é este documento:** o mapa da fase 4 fatiado em sessões. Cada sessão ainda passa pelo rito completo (plano próprio em `docs/sessions/NNNN-*.md`, decisões do dono, advisor) antes de executar. Este roadmap autoriza a DIREÇÃO, não o escopo de cada sessão.
> **Decisão de metodologia (D40):** o template dev da fase 4 é o **AI-DLC v1.4** do dono (base Matt Pocock), substituindo o `dev-bmad` da spec. Emenda à spec + ADR próprio ANTES da primeira sessão de execução.
> **D39:** núcleo mandatório Grill → PRD → DAG → TDD → Review → Release → Learn; Research/Spike/QA opcionais por demanda (opcionalidade = transições da state machine, NUNCA campo interpretado).

---

## Mapeamento AI-DLC → Kubo (fixado)

| Planilha (v1.4) | Kubo |
|---|---|
| Passo/Fase | Estado da state machine do board |
| Papel do Agente | Persona (prompt + executor + skills) |
| Skill/Playbook (grill-me, to-prd, to-issues, tdd, code-review) | Skills de persona (spec §3.2) — binding estado→persona→ação é CÓDIGO no FLOW_REGISTRY, nunca YAML |
| Validador humano + Gate de conclusão | Gate (ADR-0018) |
| Saída/Artefato (ata, RESEARCH.md, PRD, DAG) | Deliverable no grafo (família do kind=pr) |
| Knowledge Vault | O grafo de conhecimento do Kubo |
| 5 papéis humanos (PO/DevSr/TechLead/QA/AIChampion) | 1 dono (todos os validadores) |
| Confluence/Jira/Octane/Gluon/Devin | Board da UI + deliverables + GitHub + executor cli |
| DAG blocks/is-blocked-by | Aresta `blocks` da spec (adiada na 0013 — o consumidor chegou) |

**Regra transversal (achado 3 do advisor — NÃO PERDER):** o AI-DLC não tem passo "consultar o que o sistema já sabe"; a spec chama isso de diferencial (`task -[consults]-> distilled`). As personas de **Grill, Research e PRD consultam o grafo** (busca semântica) como parte do behavior, com arestas `consults` gravadas. A emenda à spec crava isso.

## As sessões (ordem de ataque)

| # | Sessão | Entrega | Notas do advisor |
|---|---|---|---|
| **0017** | **Emenda spec + ADR de metodologia** (docs, Cowork) | Spec §2.5/§4/§5: dev-bmad → dev-aidlc; cenário canônico reescrito no fluxo novo com `consults` cravado no Grill/Research/PRD; ADR-0020 (decisão de metodologia + tabela de mapeamento + D39); planilha convertida a markdown em `docs/method/fluxo-aidlc-v1.4.md`. | ANTES de qualquer execução — sessões seguintes consomem a spec emendada |
| **0018** | **Rito de promoção** (spec §3.4, = passo 9 Release) | Worker sob contrato em PR (catálogo YAML **no MESMO PR do código** — um merge, um gate) → merge do dono → deploy → botão **"Confirmar promoção"** na UI (valida via API do GitHub `merged:true`+SHA — estrutura da API, nunca confiança no clique) → validação de contrato (worker importável + manifest) → instancia pipeline → agenda. | **Deploy-gap nomeado no ADR como desvio consciente da §3.4:** worker é código Python que precisa estar na imagem (WORKER_REGISTRY hardcoded, ADR-0010) — "merge dispara" vira "dono confirma pós-deploy". SEM poll, SEM carga dinâmica de código, jamais. SEM webhook (Tailscale-only) |
| **0019** | **UI mobile** (`docs/sessions/0019-ui-mobile.md`) | O Kubo no bolso: bottom tab bar, gates operáveis no aparelho real via tailnet, gramática do kit `docs/design/v3/` (D50, supersede `mvp/`); norma "toda tela nasce responsiva". Desktop intocado. | Renumeração deste roadmap (D-numeração da sessão 0019): sessões antigas 0019-0024 avançam duas casas |
| **0020** | **Exposição internet** (`docs/sessions/0020-*.md`) | D46-D48 — expor o Kubo além do Tailscale-only da fase 1/DEV (fronteira de rede, auth, TLS). | Sessão separada da 0019 por escopo de segurança |
| **0021** | **Template pipeline** + trigger `flow_event` + cron | Execuções de pipeline como flows/cards (queued→collecting→distilling→stored\|failed); a promoção da 0018 passa a instanciar de verdade. Toca o scheduler. **D51:** o primeiro uso real do worker `github-releases` = **lista de watches do dono** (`/user/subscriptions`) como fonte de curadoria, não lista estática no `schedules.yaml`. | Separada da 0018 — juntas estouram o timebox |
| **0022** | **Tasks interativas do Humano** (= passo 1, Grill) | Task-pergunta que bloqueia até resposta do dono **pela UI** (notifica por Telegram, responde na UI — inbound de bot = fila disfarçada, §1.2, NÃO); cada rodada = task NOVA do Humano (audit trail); resposta retoma o flow um passo síncrono no request (idioma EXATO do gate 0015 — repetir, não "melhorar"); ata do Grill como deliverable. | Emenda consciente ao ADR-0018 (D38: nova porta de escrita sancionada, transacional, staleness). Gatilho (b) NÃO dispara: retomada = chamada api de segundos; TDD segue disparado por CLI |
| **0023** | **dev-aidlc fatia 1: PRD** | Template `dev-aidlc` + persona redator-PRD (consultando o grafo) + estado PRD com gate. Fatia fina completa: ata do Grill → PRD → tua aprovação. | — |
| **0024** | **dev-aidlc fatia 2: DAG** | Persona decompositora (`to-issues`) → tasks conectadas por `blocks`; validação de aciclicidade NA CRIAÇÃO (check topológico, falha alto); guarda de recusa no runtime (StateError se bloqueador aberto — a tranca, não o motor); board destaca tasks "prontas". | **O dono é o scheduler:** runtime NUNCA escolhe a próxima task; dispara-se uma a uma. "Executar todas as prontas"/avanço automático/retry = orquestrador = gatilho (b), ADR próprio + dono na mesa |
| **0025** | **dev-aidlc fatia 3: execução + revisor** | Dev (reusa dev-mini) executa tasks do DAG; persona revisora em **sessão cli independente com contexto limpo**, output = deliverable de review alimentando o gate humano — revisor NUNCA aprova (guardrail R8 da planilha por construção). | — |
| **0025b** | **Container-irmão** (condicional) | Gatilho do ADR-0019 E6: antes de conteúdo coletado/derivado entrar no circuito do executor cli (specs de PRD derivadas do grafo já contam), o agente migra para container isolado. Contrato "prompt in → stream out" sobrevive. | Provavelmente obrigatória antes da 0025 rodar com conteúdo real |
| **0026** | **Cenário canônico + Learn** | Demanda real de ponta a ponta pelo dev-aidlc (teste de aceitação da spec §4 adaptado); rito Learn & Improve (aprendizados viram PRs em playbooks/ADRs, nunca só ata); estados opcionais Research/Spike/QA entram como dados (transições + personas). **Fase 4 e roadmap da spec encerrados.** | — |

## D51 — Sinal de curadoria do `github-releases` (decidido 2026-07-15, Cowork)

O worker promovido na 0018 nasce com **lista estática** (o smoke prova o RITO de promoção, não o worker). O uso real entra na **0021**:

- **Sinal = WATCH** (`GET /user/subscriptions`, REST — sem GraphQL), **confirmado por evidência em 2026-07-16** (ver "Veredito" abaixo). Watch é a palavra certa no vocabulário do dono e do GitHub; estrela significa "gostei". **Pré-condição não-óbvia: os repos têm de estar em `All Activity`, não em `Custom`** — Custom é invisível para as duas APIs. Estrela ficou como plano B descartado. Fork foi descartado antes: obriga a resolver `parent` (N+1) e paga uma cópia de repo pelo preço de um marcador — fork volta a ser o que é, "quis mexer".
- **Pré-condição do dono: faxina ÚNICA em [github.com/watching](https://github.com/watching)** — sem desligar nada. O GitHub **removeu o auto-watch em 2025-05-22** ([changelog](https://github.blog/changelog/2025-05-22-sunset-of-automatic-watching-of-repositories-and-teams/), [discussão #157470](https://github.com/orgs/community/discussions/157470)): a feature não existe mais, a torneira já está fechada na origem. MAS o sunset **não foi retroativo** — inscrições acumuladas na era do auto-watch persistem (relatos de 900k+). Contas antigas têm entulho: faxina única resolve, não é imposto recorrente.
- **Correção de premissa (erro registrado no planejamento):** participar de uma conversa (comentar/@mention) inscreve na **thread**, NÃO no repositório — thread subscription ≠ repo subscription. A lista de watches de repo nunca foi poluída por participação. O argumento "watch é gesto misturado, estrela é gesto puro" era FALSO; watch é tão deliberado quanto estrela, e é a palavra certa.
- **Verificação por evidência antes de QUALQUER código** (disciplina ADR-0005) — **CONCLUÍDA em 2026-07-16** (ver "Veredito" abaixo): o risco era real, mas não do jeito hipotetizado. `Custom → Releases only` não faz o repo reportar `subscribed: false` — faz o endpoint de subscription devolver **404**, e some das DUAS APIs (REST e GraphQL), não só de uma. Decisão resultante para a 0021: coletar via `All Activity` (a API enxerga) e mover a curadoria de entrega para `settings/notifications`, nunca para o nível de subscription.
- **Descoberta ≠ curadoria (não misturar):** "top repos do mês" fica em item PRÓPRIO na fila (ver abaixo). Envenenar a curadoria com descoberta é o jeito clássico de matar o digest.

### Veredito da verificação — WATCH CONFIRMADO (executado 2026-07-16, evidência do dono no Mac)

**O sinal é watch (`/user/subscriptions`). Estrela descartada.** A verificação rodou ANTES de qualquer código, como a disciplina exige — e, no caminho, produziu duas conclusões erradas que só caíram porque o dono insistiu. Ambas registradas abaixo: o processo importa mais que o veredito.

**O achado real: `Custom` é invisível para a API; só `All Activity` aparece.**

| Fonte | Custom (releases-only) | All Activity |
|---|---|---|
| `github.com/watching` (UI) | `Custom 136` | idem |
| `gh api user/subscriptions` (REST) | `[]` | **136** |
| `gh api graphql viewer.watching` | `totalCount: 0` | **conta certo** |
| `gh api repos/{o}/{r}/subscription` | `404 Not Found` | `{"subscribed": true}` |

O GitHub modela subscription como binário (`subscribed`/`ignored`); watch **customizado** não cabe nesse modelo e some das DUAS APIs. Um repo listado na UI dá 404 no endpoint de subscription. Não é bug do desenho do Kubo — é o modelo da API.

**A saída, que é o desenho mais honesto: inscrição ≠ entrega.** Marcar `All Activity` inscreve (a API enxerga); o barulho se mata em `settings/notifications`, desmarcando a entrega da linha **Watching** — que **não** desinscreve. A linha **Participating** é independente e segue avisando @menção/resposta. Usar `Custom` para filtrar era terceirizar ao GitHub a curadoria que o Kubo faz — e foi exatamente isso que cegou a API. **A inscrição vira SINAL (o Kubo lê); a notificação é entrega, e quem entrega é o Kubo.**

**Migração executada (one-time, 2026-07-16) — a lista da 0021 tem 260 repos:**

| Etapa | Número |
|---|---|
| Watches em Custom, convertidos a All Activity (`PUT .../subscription -F subscribed=true`) | **136** (136/136 ok) |
| Forks do dono → `parent` deduplicado | **348** |
| Desses, com release publicada nos últimos 90 dias (**filtro de vivo**) | **196** (56%) |
| União (196 ativos ∪ 136 watches), inscritos os que faltavam | **260** (+124) |

**Fork ENTROU como fonte, mas só o parent e só se vivo.** Os 152 fork-parents sem release em 90 dias ficaram de fora: custariam uma chamada por rodada para devolver vazio. Fork morto continua fork; ressuscitou, o dono inscreve. Isto NÃO reabre "fork é o sinal" — o sinal continua sendo a watch list; o fork foi só o **material de origem** de uma migração one-time, resolvendo `parent` uma vez, na mão do dono, não a cada rodada do worker.

**Ovo-e-galinha nomeado:** a API **não lista** o que não enxerga, então os 136 nomes do Custom tiveram de sair do HTML das 6 páginas de `github.com/watching` — não há caminho por `gh`/GraphQL para essa extração. Os forks, ao contrário, a API lista (`gh repo list --fork --json parent`) — mas **`parent` não tem `nameWithOwner`**, só `name` + `owner.login`: pedir o campo errado devolve `null` silencioso em todos, e o `sort -u` colapsa 348 em 1 linha que parece um resultado. Terceira falha silenciosa da noite.

**Regressão futura silenciosa:** se o dono voltar um repo a Custom, ele some do coletor **sem erro**. O worker não tem como distinguir "desinscrito" de "customizado".

**Volume esperado (estimativa, medir na 0021):** 260 repos vivos → ordem de 7-15 releases/dia. Custo de destilação irrelevante; o custo real é o tempo de leitura do dono. **Isto torna a decisão de backfill (item 4 abaixo) crítica, não cosmética:** com `per_page=30` e backfill, a estreia destila até 7.800 releases. Começar do zero mantém a estreia vazia e o regime permanente no número acima.

**Custo remanescente (real, aceito):** ler a watch list exige token com escopo **`notifications`** — mais largo que "ler release de repo público", que é tudo que o `GITHUB_TOKEN_READONLY` faz hoje. `/user/starred` não pediria isso. **Item da 0021:** decidir se o coletor ganha `notifications` no PAT existente ou um PAT próprio (least-privilege, invariante 8).

**Três falhas silenciosas encontradas no caminho (o mais valioso deste bloco — registrar):**

1. **Sem o escopo `notifications`, `/user/subscriptions` devolve `[]` SEM ERRO.** A primeira rodada parecia confirmar o defeito do desenho e só refletia o token. Foi o `gh` avisando ("This API operation needs the `notifications` scope") que separou as causas.
2. **`gh api -f subscribed=true` manda a STRING `"true"` → `422 Validation Failed`.** O correto é `-F` (tipado). A primeira versão do script escondia stderr com `2>/dev/null` e imprimiu 12 `FALHA` seguidas — que pareciam confirmar "watch não dá", quando era erro de sintaxe. **Falha silenciosa mascarada de evidência** — o mesmo pecado apontado no D51 #2 (teste do teto de bytes), cometido duas horas depois por quem apontou.
3. **Pedir `parent.nameWithOwner` devolve `null` silencioso** (o campo não existe em `parent`, só `name` + `owner.login`) — os 348 forks colapsaram num `sort -u` de 1 linha que parecia um resultado válido. Sem erro, sem stderr: só um número errado que passaria batido sem conferência manual.

**Erro de método registrado (custou duas reversões):** declarei "watch morreu, estrela ganha" com base na REST apenas; o dono empurrou → testei GraphQL (também 0) e declarei de novo; o dono empurrou de novo → a hipótese certa (Custom vs All Activity) só apareceu na terceira. As duas primeiras conclusões vieram de **parar de procurar cedo demais**, não de dado errado. O instinto do dono ("watch é a palavra certa") estava certo o tempo todo; foi a exploração da API que estava incompleta. **Conclusão só depois de esgotar a explicação chata** — mesma lição de [[verificar-fato-na-fonte]] e do E3/ADR-0019.

**Correção (achado do advisor, sessão 0021 — este parágrafo ficava órfão e mentindo em 6 meses):**
a migração dos 136 Custom para All Activity está FEITA, não é mais tarefa pendente — a lista de
watch é 260 repos, e é isso que o worker `github-releases` v0.2.0 lê (ADR-0022). A ideia original
deste parágrafo (estrelar os repos desejados como filtro de curadoria) NÃO foi implementada — a
0021 decidiu (D52) neutralizar a enxurrada de estreia com `since` congelado, sem camada de
curadoria por estrela. A curadoria dos 260 continua uma dívida real e nomeada (ADR-0022,
consequências): a lista tem entulho visível (forks alheios, repos de 0 estrela) e o custo — tempo
de leitura do dono — só aparece depois de medir a primeira semana de digest. O mecanismo de poda,
se a curadoria mostrar necessidade, é `unwatch` no próprio GitHub (o produto funcionando como
desenhado, não manutenção do Kubo).

### Dívida herdada do worker (achados da revisão do PR #50 pelo dono/Cowork, 2026-07-15 — mergeados conscientemente, a 18b prova o RITO, não a perfeição do worker)

1. **`403` NÃO é rate limit — os stats mentem.** `github_releases.py` classifica todo 403 como `kind="rate_limit"`. Na API do GitHub 403 é quase sempre **permissão negada** (PAT expirado, repo privado, SSO); rate limit real vem com `x-ratelimit-remaining: 0` ou `retry-after`. Sintoma: PAT expira → digest reporta `rate_limited: 1` → o dono espera a janela passar em vez de arrumar o token. **Fix: distinguir pelos headers.** Nota de acusação: o agente NÃO errou — o enunciado da task dizia "rate limit (403/429)"; ele implementou fielmente o bug do enunciado. Custo do erro é de quem escreve o enunciado.
2. **O teste do teto de bytes cobre o caminho fácil.** `test_response_exceeding_byte_cap_is_rejected_without_buffering` só exercita o header `content-length` DECLARADO. O laço de streaming (`iter_raw` + acumulador) — a defesa real — está **sem teste**, e é justamente o caminho do ataque (resposta gigante via chunked, sem `content-length` honesto). Código correto, cobertura mentirosa. Follow-up barato, pode sair antes da 0021.
3. **Nit:** `_owner_repo_shape` valida "uma barra, sem `..`" mas deixa passar `acme/widget?x=1` → query injection na URL montada. Baixo (config do dono, não input hostil), mas o validador promete mais do que cumpre. Whitelist de chars (`[A-Za-z0-9._-]` por parte) resolve.
4. **`per_page=30` sem paginação** (decisão v1 deliberada, upsert idempotente cobre re-coleta): a primeira rodada traz **até 30 releases por repo**. Responde parcialmente a pergunta de backfill — mas com N repos do watch list, 30 cada ainda é **enxurrada de estreia** + custo de destilação por item. **Decisão em aberto na 0021: backfill vs começar-do-zero.** Recomendação do planejamento: começar do zero (o dono quer acompanhar, não arquivar).
5. **Falta `published_at`/`updated_at` no metadata** — sem isso o digest não sabe dizer "saiu quando". Gap de produto, não bug.

## Fila (itens nomeados, sem sessão ainda)

- **Worker de trending (descoberta).** Intenção do dono: acompanhar o que está bombando. Achados do planejamento: "top 100 por estrelas" *all-time* é lista praticamente ESTÁTICA (linux, react, freeCodeCamp) — o que se quer é **trending** (estrelas GANHAS no período), que o GitHub **não expõe em API oficial** (a página `/trending` é HTML). Proxy honesto via search API: repos criados recentemente ordenados por estrelas. **Produto certo NÃO é coletar releases de 100 repos** (custo de API + custo de destilação por item, digest vira firehose): é um **digest de descoberta**, do qual o dono promove ao watch o que interessar — descoberta alimenta curadoria pela MÃO do dono, nunca pelo cano. Decisão + advisor próprios.
- **Mecânica de disparo do passe adversarial no caminho normal do rito (ADR-0021 §XI).** O smoke da 18b só exercitou o passe de `security-reviewer` dentro do fallback D44 (exceção, antes de reabrir o PR). Falta decidir COMO ele dispara quando o agente NÃO tropeça: automático como job do CI sobre o diff do PR do agente, ou manual pela thread principal antes de notificar o dono do gate aberto? O slot já está fixado na ADR (roda sobre o diff, antes da decisão do gate); a mecânica de disparo é o que falta.
- **Apertar o allowlist do `agent-path-guard` para `catalogs/integrations/`** (ADR-0021 §XIII). Hoje o allowlist libera `catalogs/` inteiro pra PR de branch `agent/*` — inclui `catalogs/personas/` (least-privilege do projeto) e `catalogs/flow_templates/`. Mudança de CI, não de arquitetura; o gate humano na UI ainda vê o diff antes de aprovar, mas apertar fecha uma folga de defesa-em-profundidade barata.
- **Guard-bash não bloqueia commit direto em `main`** (achado da sessão 0018b: um commit acidental do ADR-0021 foi parar em `main` local, pego por disciplina — checar `git log`/`git status` antes de prosseguir —, não por trava). O CLAUDE.md promete "duas camadas de enforce: guard-bash barra localmente" pro fluxo de branch, mas a camada local hoje só cobre CRIAÇÃO de branch fora da taxonomia — não cobre `git commit` com `HEAD` já em `main`. Conserto por PR no hook: checar `git rev-parse --abbrev-ref HEAD` e negar o commit se for `main`. Nunca contornar (nem com `--no-verify`, que a política do harness já proíbe por padrão).

## Guardas anti-DSL (do advisor — valem para TODAS as sessões)

1. Skill por estado no YAML = a rampa da DSL. Skills pertencem à persona; binding é código.
2. Config por estado (timebox, limite de linhas, rodadas do Grill) = constantes de behavior/config de worker, NUNCA campos do template.
3. Campo `optional:` = runtime interpretando dado para decidir = PROIBIDO. Opcionalidade já mora nas transições.
4. Em Python: primitivas genéricas (guarda de bloqueio, prontidão) como funções burras na store/runtime; C901 vigiado — workflow engine também nasce disfarçado de helper.

## Gatilhos de reabertura (nomeados)

- Grill exigir raciocínio pesado por pergunta (retomada não cabe em request) → processo executor por ADR antes da 0022.
- Promoção frequente a ponto de o botão virar fricção → poll como job do scheduler EXISTENTE, nunca processo novo.
- DAG com 15+ tasks e disparo manual virar fricção medida → processo executor pelo caminho nomeado.
- Workers gerados fora do repo do Kubo → registro vira PR-de-catálogo aberto pelo Kubo (dois merges), deploy-gap ganha um passo.
