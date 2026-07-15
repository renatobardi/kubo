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
| **0021** | **Template pipeline** + trigger `flow_event` + cron | Execuções de pipeline como flows/cards (queued→collecting→distilling→stored\|failed); a promoção da 0018 passa a instanciar de verdade. Toca o scheduler. | Separada da 0018 — juntas estouram o timebox |
| **0022** | **Tasks interativas do Humano** (= passo 1, Grill) | Task-pergunta que bloqueia até resposta do dono **pela UI** (notifica por Telegram, responde na UI — inbound de bot = fila disfarçada, §1.2, NÃO); cada rodada = task NOVA do Humano (audit trail); resposta retoma o flow um passo síncrono no request (idioma EXATO do gate 0015 — repetir, não "melhorar"); ata do Grill como deliverable. | Emenda consciente ao ADR-0018 (D38: nova porta de escrita sancionada, transacional, staleness). Gatilho (b) NÃO dispara: retomada = chamada api de segundos; TDD segue disparado por CLI |
| **0023** | **dev-aidlc fatia 1: PRD** | Template `dev-aidlc` + persona redator-PRD (consultando o grafo) + estado PRD com gate. Fatia fina completa: ata do Grill → PRD → tua aprovação. | — |
| **0024** | **dev-aidlc fatia 2: DAG** | Persona decompositora (`to-issues`) → tasks conectadas por `blocks`; validação de aciclicidade NA CRIAÇÃO (check topológico, falha alto); guarda de recusa no runtime (StateError se bloqueador aberto — a tranca, não o motor); board destaca tasks "prontas". | **O dono é o scheduler:** runtime NUNCA escolhe a próxima task; dispara-se uma a uma. "Executar todas as prontas"/avanço automático/retry = orquestrador = gatilho (b), ADR próprio + dono na mesa |
| **0025** | **dev-aidlc fatia 3: execução + revisor** | Dev (reusa dev-mini) executa tasks do DAG; persona revisora em **sessão cli independente com contexto limpo**, output = deliverable de review alimentando o gate humano — revisor NUNCA aprova (guardrail R8 da planilha por construção). | — |
| **0025b** | **Container-irmão** (condicional) | Gatilho do ADR-0019 E6: antes de conteúdo coletado/derivado entrar no circuito do executor cli (specs de PRD derivadas do grafo já contam), o agente migra para container isolado. Contrato "prompt in → stream out" sobrevive. | Provavelmente obrigatória antes da 0025 rodar com conteúdo real |
| **0026** | **Cenário canônico + Learn** | Demanda real de ponta a ponta pelo dev-aidlc (teste de aceitação da spec §4 adaptado); rito Learn & Improve (aprendizados viram PRs em playbooks/ADRs, nunca só ata); estados opcionais Research/Spike/QA entram como dados (transições + personas). **Fase 4 e roadmap da spec encerrados.** | — |

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
