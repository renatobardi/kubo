# Kubo (工房) — Especificação Funcional

> **Versão:** 0.1 (documento-fundador)
> **Data:** 2026-07-04
> **Status:** Draft para revisão
> **Autor:** Renato Bardi (com apoio de Claude)

---

## 0. Contexto e motivação

O Kubo nasce do colapso deliberado do ecossistema RARA/Kura (Go + Elixir + Python, CQRS cross-runtime, Neon PostgreSQL + SurrealDB) em um sistema único, mantível por uma pessoa. A causa-raiz do reinício é **fadiga de complexidade**: o custo de operar três runtimes e dois bancos superou o valor entregue.

O Kubo **não é um fork**. Ele rouba especificações funcionais e padrões de três referências, sem herdar nenhum codebase:

| Referência | O que o Kubo absorve | O que o Kubo rejeita |
|---|---|---|
| **Valmis** | Modelo de agents + workflows; catálogo "1 YAML por integração"; memória em 4 categorias; design system completo (ver `kubo-design-system.md`) | Proxy de credenciais (threat model corporativo multi-tenant); pgvector; monorepo TS/Svelte; workflow canvas builder |
| **Multica** | Personas; fluxo BMAD; projetos/issues/tasks/kanban como modelo de trabalho | Coreografia fixa de 13 agentes; kanban como produto/UI de primeira classe |
| **RARA/Kura** | Workers de produção (scribe, feed, harvest) como templates; orquestração provider-agnostic (LiteLLM); tese do grafo de conhecimento (document + vector + graph) | CQRS poliglota; dois bancos; três linguagens |

**Princípio-guia:** um runtime, um banco, três catálogos, quatro capacidades. Toda decisão de design é julgada contra a pergunta: *isso aumenta ou reduz a carga cognitiva de um mantenedor solo?*

---

## 1. Visão

**Kubo é um ateliê pessoal de agentes: um sistema que coleta informação, cura conhecimento em grafo, distribui resultados — e fabrica os próprios trabalhadores.**

O nome (工房, *kōbō*) significa "ateliê / oficina do mestre artesão" — par natural do Kura (蔵, armazém). A metáfora é operacional: no ateliê, as ferramentas fazem ferramentas.

### 1.1 As quatro capacidades

1. **Coletar** — agentes e workers determinísticos ingerem fontes (YouTube, RSS, HN, páginas, APIs) sob demanda, agendados ou por evento.
2. **Curar e guardar** — o conteúdo bruto é destilado e armazenado em base consultável e curada: documentos + embeddings + grafo de relações, num único datastore.
3. **Distribuir** — o conhecimento sai do sistema: dashboards, relatórios, e-mail, Telegram — para o dono e para convidados (amigos).
4. **Auto-estender** — o sistema desenvolve novos agentes e workers como *projetos*, com personas trabalhando via kanban/issues/tasks, com acesso ao GitHub, sob supervisão humana.

A capacidade 4 é consumidora das capacidades 1–3: agente que desenvolve agente consulta a base de conhecimento (2), reporta ao humano pelos canais de distribuição (3) e entrega workers que alimentam a coleta (1). **O ciclo se retroalimenta.**

### 1.2 O que o Kubo NÃO é (escopo negativo)

Escopo negativo é contrato, não sugestão. Cada item abaixo foi considerado e rejeitado com razão registrada:

- **Não é plataforma multi-tenant.** Um dono, uma VPC (a existente na Oracle Cloud/OCI). Amigos são *destinatários* de distribuição, não usuários que executam agentes. Consequência: **sem proxy de credenciais** — secret manager + permissões por persona (declaradas em YAML) cobrem o threat model. Reavaliar apenas se amigos virarem operadores.
- **Não é workflow engine genérico.** Sem canvas builder, sem DSL de workflow. Templates de Flow são YAML declarativo; lógica que YAML não expressa pertence a skill de persona ou a worker, nunca ao template.
- **Não é ferramenta de project management standalone.** Kanban/issues/tasks são *modelo de dados no grafo* + views; não competem com produtos de PM. Sem drag-and-drop como prioridade, sem sync externo de PM.
- **Não adota orquestrador pesado na largada.** Sem Prefect/Dagster/Temporal/Airflow. APScheduler + webhooks FastAPI. A migração futura é localizada porque pipeline é entidade no grafo.
- **Não constrói UI rica na fase 1.** A view é descartável; o grafo não. UI mínima (FastAPI + HTMX ou TUI) até o substrato provar valor.
- **Não persegue autonomia total.** Promoção de código gerado a pipeline operacional **sempre** passa por gate humano. Proibido por spec neste estágio.

---

## 2. Arquitetura

### 2.1 Decisões estruturais

| Decisão | Escolha | Racional |
|---|---|---|
| Runtime | **Python 3.12+ único** (FastAPI, APScheduler) | Centro de gravidade do ecossistema de IA (LiteLLM, bindings whisper, SDKs de agentes). Elimina o poliglota Go/Elixir. |
| Datastore | **SurrealDB único** (document + vector + graph) | Multi-model elimina o segundo banco. O grafo é load-bearing: conhecimento E trabalho vivem nele. |
| Camada LLM | **LiteLLM** (providers via API) + **executors CLI** (agentes locais) | LiteLLM já dominado (rara-mind). CLIs agênticos são abstração separada (§2.4). |
| Deploy | **Docker Compose único** na VPC existente da **Oracle Cloud (OCI)** | A topologia que motivou o olhar pro Valmis: um compose, sobe tudo — numa compute instance da VCN já provisionada; sem serviços gerenciados de outra cloud. |
| Configuração | **3 catálogos YAML** versionados em Git | Integrações, personas, templates de flow — uma mecânica só (§2.5). |
| Front | FastAPI + HTMX (fase 1), design system do Valmis | Ver `kubo-design-system.md`. Tokens portáveis, view descartável. |

### 2.2 Topologia

```
┌──────────────────── VPC (Oracle Cloud/OCI) ────────────────┐
│  docker compose                                            │
│  ┌─────────────────────────────┐   ┌────────────────────┐  │
│  │  kubo (Python)              │   │  SurrealDB         │  │
│  │  ├─ api (FastAPI)           │◄──┤  document          │  │
│  │  ├─ scheduler (APScheduler) │   │  vector (HNSW)     │  │
│  │  ├─ runtime de flows/tasks  │   │  graph             │  │
│  │  └─ executors (api|cli)     │   └────────────────────┘  │
│  └──────────┬──────────────────┘                           │
│             │ LiteLLM ──► providers (Anthropic, OpenAI,    │
│             │             Gemini, OpenRouter, Ollama local)│
│             │ CLI adapters ──► claude code, gemini, goose  │
│             │ Integrações YAML ──► GitHub, Telegram, SMTP, │
│             │                      RSS, YouTube, HN...     │
└─────────────┴──────────────────────────────────────────────┘
```

Segredos: OCI Vault (ou `.env` cifrado na instância) referenciado por nome nos YAMLs — nunca valores inline.

### 2.3 Modelo de dados (SurrealDB)

Dois schemas vizinhos **no mesmo banco**, com arestas entre eles — o diferencial que nem Valmis nem Multica têm: trabalho em andamento consulta conhecimento acumulado.

#### Schema de conhecimento (herança Kura)
```
source      (origem: canal YT, feed RSS, site, API)
item        (unidade coletada bruta: vídeo, post, artigo)
distilled   (destilado: resumo, claims, entidades extraídas)
entity      (conceito/pessoa/tecnologia/organização)
memory      (4 categorias — episódica, semântica, procedural, working)

arestas:
item      -[from_source]->   source
distilled -[derived_from]->  item
distilled -[mentions]->      entity
entity    -[relates_to]->    entity        (tipada: uses, competes, part_of...)
memory    -[grounded_in]->   distilled|item
```
Embeddings como campos vector nos records `distilled` e `memory` (índice HNSW). O modelo de memória em 4 categorias (roubado do Valmis) fica *melhor* aqui que na origem: episódica/semântica/procedural com relações é problema de grafo — pgvector não expressa as arestas.

#### Schema de trabalho (herança Multica)
```
flow_template  (catálogo, versionado — ver §2.5)
flow           (instância viva de um template, com snapshot da config)
board_state    (estados do kanban do flow, definidos pelo template)
task           (unidade de trabalho)
persona        (papel: prompt + provider + executor + skills + permissões)
deliverable    (worker, execução, relatório, repo)
git_repo       (repositório associado — obrigatório em flows dev)

arestas:
flow        -[instance_of]->  flow_template
task        -[belongs_to]->   flow
task        -[in_state]->     board_state
task        -[assigned_to]->  persona
task        -[blocks]->       task
flow        -[has_repo]->     git_repo          (obrigatório se o template exigir)
flow        -[produces]->     deliverable
flow        -[produces]->     flow              (dev → pipeline: proveniência permanente)
deliverable -[registered_as]-> source|worker    (fecha o ciclo com a coleta)
```

#### Arestas cross-schema (o diferencial)
```
task      -[consults]->   distilled|entity   (agente citou conhecimento ao trabalhar)
distilled -[produced_by]-> flow              (execução de pipeline que gerou o conhecimento)
```

### 2.4 Abstração de execução

Toda persona declara **um executor**; o resto do sistema não sabe a diferença.

| Executor | Mecanismo | Uso típico |
|---|---|---|
| `api` | LiteLLM → provider (Anthropic, OpenAI, Gemini, OpenRouter; Ollama local via API OpenAI-compatible) | Destilação, análise, chat, personas de raciocínio |
| `cli` | Adapter de subprocess com contrato comum (`prompt in → stream de eventos out`). Claude Code via **Claude Agent SDK** (Python); demais (gemini, goose) via adapter fino | Personas dev que precisam de filesystem, git, execução de código |

Roteamento por persona no YAML: `persona.arquiteto → claude-opus`, `persona.dev → executor cli (claude code)`, `persona.destilador → modelo barato/local`. Fallbacks e budget por flow declarados no template.

### 2.5 Os três catálogos (uma mecânica)

Diretórios de artefatos YAML declarativos, versionados em Git, que o runtime materializa. Mesma mecânica do padrão "1 YAML por integração" do Valmis, generalizada:

```
catalogs/
├── integrations/    # 1 YAML por conector (github.yaml, telegram.yaml, rss.yaml, smtp.yaml...)
│                    #   auth por referência a secret, endpoints, rate limits
├── personas/        # 1 YAML por papel (analista.yaml, arquiteto.yaml, dev.yaml, reviewer.yaml,
│                    #   humano.yaml, destilador.yaml, operador.yaml)
│                    #   prompt, executor, provider/modelo, skills, permissões, emoji
└── flow_templates/  # 1 YAML por template (dev-bmad.yaml, dev-speckit.yaml, pipeline.yaml...)
```

O catálogo pode morar em repo Git próprio — o que torna os templates editáveis, no futuro, por um flow do próprio Kubo (fase 4 aplicada a si mesma).

---

## 3. Conceitos funcionais

### 3.1 Flow — o conceito unificado

Não existem "tipos de projeto" primitivos. Existe **um conceito — Flow — parametrizado por template**. "Projeto dev BMAD", "Projeto dev Spec-kit" e "Pipeline" são templates diferentes do mesmo mecanismo.

Um **flow template** declara:

1. **State machine do board** — estados e transições válidas.
   - `dev-bmad`: `backlog → analysis → in_progress → review → done → promoted`
   - `pipeline`: `queued → collecting → distilling → stored | failed` (retry = card volta de estado)
2. **Cast de personas** — quais papéis o flow instancia, com config completa de cada um. Todo cast inclui a persona **Humano**.
3. **Gates** — transições que exigem task da persona Humano. O rito de promoção é *um gate declarado* no template dev (`done → promoted`), não regra hardcoded.
4. **Contrato de deliverable** — o que o flow produz. Dev: `worker sob contrato + git_repo` (repo obrigatório, campo `has_repo` validado na instanciação). Pipeline: `execuções + registros no grafo de conhecimento`.
5. **Triggers** — `manual | cron | webhook | flow_event`. "Dev promovido" → instancia flow Pipeline automaticamente (a relação `produces` é uma transição declarada).
6. **Budget** — limites de custo LLM por flow.

**Regras invioláveis:**
- Templates são **dados, não código**. Sem condicionais, sem herança, sem hooks programáveis. (Escopo negativo §1.2.)
- **Template versionado, instância snapshot.** Instanciar = copiar config congelada pro flow. Editar template não afeta flows em andamento.
- Instanciar template = materializar registros no grafo (flow + board_states + personas + gates) + side effects declarados (ex.: criar repo GitHub).

### 3.2 Persona

Persona = **dado declarativo**, não subsistema:

```yaml
# catalogs/personas/arquiteto.yaml
name: arquiteto
emoji: "📐"
executor: api
model: claude-opus-4-8        # via LiteLLM; fallback declarável
prompt: |
  Você é o arquiteto do ateliê Kubo...
skills: [design-de-workers, surrealdb-schema, avaliacao-de-fontes]
permissions:
  integrations: [github:read]
  knowledge: read
  tasks: [create, transition]
```

#### A persona Humano
O human-in-the-loop **não é mecanismo especial** — é uma persona no grafo cujas tasks **bloqueiam até resposta**. Consequências:
- Tasks do Humano entram no mesmo state machine e no mesmo kanban que as demais.
- A **notificação** usa a própria capacidade de distribuição (Telegram/e-mail): o sistema usa a si mesmo para falar com o dono.
- Toda decisão humana fica registrada no grafo — audit trail nativo.
- Gates (§3.1) são implementados como tasks do Humano criadas automaticamente na transição.

### 3.3 Worker e o contrato

Worker = unidade executável determinística (com LLM em junções definidas, não "agente que descobre tudo").

**Contrato de worker (obrigatório para todo deliverable de flow dev):**
```python
class Worker(Protocol):
    manifest: WorkerManifest   # nome, versão, integrações usadas, schema de config
    def run(self, ctx: RunContext) -> RunResult: ...
    # RunContext: config, acesso a integrações (por permissão), cliente do grafo, logger
    # RunResult:  itens produzidos, métricas, erros estruturados
```

O contrato é o que torna código gerado por agente **deployável por construção**: o runtime não confia no agente — valida o contrato. Todo worker vive em repo Git (ou diretório do monorepo de pipelines), nunca solto no banco.

Os workers do RARA são portados como os **três primeiros templates**, validando o contrato com código de produção real:
- `scribe` — transcrição (whisper.cpp large-v3, beam 5, Silero VAD, PT-BR)
- `feed` — coleta RSS/HTML/HN
- `harvest` — coleta de páginas/artigos

### 3.4 O rito de promoção

O momento de maior risco do sistema: código gerado por agente virando pipeline agendada na VPC.

1. Flow dev produz worker sob contrato, em repo próprio, via PR.
2. Persona **reviewer** pré-analisa (opcional, recomendado).
3. **Gate humano obrigatório**: task da persona Humano, notificada via Telegram. Aprovação = merge.
4. Merge dispara: validação do contrato → registro do worker no catálogo → instanciação do flow Pipeline (trigger `flow_event`) → agendamento.
5. Proveniência permanente no grafo: `flow_dev -[produces]-> flow_pipeline`, com personas, decisões e tasks consultáveis para sempre.

### 3.5 Distribuição

Query sobre o grafo → artefato → canal.

- **Artefatos:** relatório (markdown/HTML), dashboard (view HTML gerada), digest.
- **Canais (integrações YAML):** Telegram bot, SMTP, arquivo. Destinatários: dono e convidados (lista declarada — amigos recebem, não operam).
- **Uso interno:** notificações de gate e de falha de pipeline usam esta mesma camada.

---

## 4. Cenário canônico: "quero ingerir posts do X"

*Ilustrativo do ciclo completo (a fonte X é deliberadamente hostil — API paga, scraping instável — para exercitar decisões humanas no fluxo).*

1. **Dono** instancia flow do template `dev-bmad`: "coletar posts do X sobre temas Y".
2. **Analista** entrevista o dono — tasks para a persona **Humano**, notificadas via Telegram. Descobre que a API do X é paga → devolve task de decisão: *API oficial vs. alternativa*.
3. **Arquiteto** desenha o worker consultando **o que o sistema já sabe** sobre coleta (aresta `task -[consults]-> distilled`) — padrões dos workers feed/harvest existentes.
4. Fluxo BMAD executa no kanban: personas **dev** (executor `cli`, Claude Code via Agent SDK) implementam em repo próprio; **reviewer** analisa; cards andam.
5. Deliverable: worker `x-collector` sob contrato, PR aberto.
6. **Gate de promoção** (§3.4): dono aprova via Telegram.
7. Nasce o flow `pipeline-x-collector` (template `pipeline`, trigger cron): cada execução vira card `queued → collecting → distilling → stored`; falhas ficam visíveis no board, sem abrir log.
8. Itens coletados entram no grafo de conhecimento com proveniência `distilled -[produced_by]-> flow` — e passam a ser consultáveis pelos **próximos** flows dev.

---

## 5. Roadmap

**Critério de cada fase: entregar valor usável antes de abrir a próxima.**

### Fase 1 — Substrato (o produto mínimo que já vence)
- Runtime Python + SurrealDB + LiteLLM; Docker Compose na VPC.
- Schemas de conhecimento e trabalho; contrato de worker; catálogos de integrações e personas.
- Porte de `scribe`, `feed`, `harvest` como templates de worker.
- **Deliverable de prova (90 dias):** conteúdo PT-BR (YouTube/RSS) entra, é destilado, vira grafo consultável com citação de origem.

### Fase 2 — Distribuição
- Query → relatório/digest → Telegram + SMTP. Dashboard HTML mínimo.
- Notificações internas (gates, falhas) sobre a mesma camada.

### Fase 3 — Modelo de trabalho
- Flow templates + instanciação; boards como views do grafo; personas executando tasks (executors api e cli); integração GitHub (branch, PR, commit).
- Sem autonomia de criação: dono cria tasks, agentes executam.

### Fase 4 — Auto-extensão
- Template `dev-bmad` completo: Analista quebra necessidade em issues, personas executam, rito de promoção fecha o ciclo.
- Cenário canônico (§4) executado de ponta a ponta como teste de aceitação.

### Fora do roadmap (registrado)
- Proxy de credenciais: somente se o sistema virar multi-operador (outro produto).
- Orquestrador pesado: somente se volume cobrar; migração localizada.
- Kanban como produto/UI rica: as views evoluem sob demanda; o grafo é o contrato.

---

## 6. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Scope creep (a doença que matou o RARA 2.0, agora em escopo) | Escopo negativo como contrato (§1.2); teste "o substrato precisa disso em 90 dias?" |
| Código gerado por agente com comportamento indevido | Contrato de worker + gate humano obrigatório + permissões por persona + repo/PR sempre |
| Templates YAML evoluírem para DSL | Regra inviolável §3.1; lógica vai para skill ou worker |
| Drift entre template e instâncias | Snapshot na instanciação (§3.1) |
| SurrealDB como aposta (maturidade) | Acesso ao banco isolado numa camada `store/` fina; schemas documentados; export periódico |
| Custo LLM descontrolado em flows autônomos | Budget por flow no template; roteamento barato/local para personas de volume |

---

## 7. Referências vivas

- `kubo-design-system.md` — UI/UX integral extraída do Valmis (tokens OKLCH, Inter + Noto Serif, componentes, app shell).
- Valmis (`valmishq/valmis`) — especificação funcional de agents/integrações/memória.
- Multica (privado) — personas, BMAD, modelo de projetos.
- RARA (`rara-scribe`, `rara-feed`, `rara-harvest`, `rara-mind`) — workers a portar e camada LiteLLM de referência.

---

*Kubo: no ateliê, as ferramentas fazem ferramentas.*
