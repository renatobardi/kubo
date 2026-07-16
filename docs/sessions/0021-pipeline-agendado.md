# Sessão 0021 — Pipeline agendado: o Kubo trabalha sozinho

> **Status:** aprovado pelo dono (2026-07-16, planejamento no Cowork); advisor **GO com correções 1-6 incorporadas**
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5) — sessão de risco MÉDIO (toca `scheduler/`, `runtime/`, `workers/`; credencial nova; nenhum dado de terceiro entra no circuito do executor cli)
> **Timebox:** 8 horas efetivas — advisor estima ~9,5h com tudo. **O corte VAI disparar.** Ponto de corte PRÉ-ACORDADO na hora ~4,5 (ver "Sacrifícios"); o restante é a **sessão 0021b, PRÉ-AUTORIZADA por este plano**, sem replanejamento.
> **Estrutura:** 3 PRs — `fix/github-releases-rate-limit` (curto, primeiro), `feat/0021-watch-list-worker`, `feat/0021-pipeline-template` (D16: título e corpo em inglês)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.
> **Pré-condição:** `docs/sessions/fase4-roadmap.md` (D51) commitado na main — este plano cita o D51 como endereço da dívida do worker.

---

## Missão

O Kubo passa a trabalhar sozinho. Todo dia, um cron instancia um flow de pipeline: o worker `github-releases` lê a **watch list do dono** (260 repos), coleta as releases publicadas **depois de um piso fixo**, e o grafo cresce sem ninguém clicar em nada. A destilação continua no cron das 09:00 que já existe. Caso de uso âncora: **o dono abre o digest de manhã e lê o que mudou nas ferramentas que ele acompanha** — sem visitar repo nenhum.

Isto é a fase 1 da spec (coletar → destilar → distribuir) fechando o laço com curadoria real, e é o primeiro consumidor do worker promovido na 0018.

## Decisões do dono

- **D52 — Começar do zero, sem backfill.** 260 repos × `per_page=30` = ~7.800 destilações na estreia. Inaceitável. O piso é `since`, escalar fixo na config do worker.
- **D53 — Fatia vertical fina.** Pipeline + agendamento + worker lendo a watch list, ponta a ponta. A dívida do worker (D51) fica fora — com UMA exceção, D55.
- **D54 — PAT próprio pro coletor** (`GITHUB_TOKEN_WATCH`, integração dedicada `github-watch`). Não alargar o `GITHUB_TOKEN_READONLY` do rito de promoção.
- **D55 — O fix do 403≠rate-limit sai em PR curto ANTES do smoke.** Exceção consciente ao D53: o advisor mostrou que a dívida ficou mais cara ao mudar a entrada — com 260 repos e um PAT novo, o modo de falha mais provável da estreia é 403-de-permissão reportado como `rate_limited: 260`, mandando o dono esperar uma janela que não existe.
- **D56 — Subir o `max_items` do distiller** (20 → 50) no mesmo PR do agendamento. O teto atual já serve 6 feeds; somar 7-15 releases/dia represaria o funil e o digest atrasaria por motivo estrutural, não pelo pipeline novo.

## Correções do advisor (GO condicionado a 1-6)

### C1 — `since` mora na config do WORKER, nunca no template
O planejamento defendeu "congelado no snapshot (invariante 4)" — **camada errada**. O snapshot congela o template (board/transitions/cast, ADR-0016 §II); config de worker mora na entry do `schedules.yaml` (ADR-0010 §II). `since` no template genérico transformaria `pipeline` em worker-específico = **violação direta da guarda anti-DSL #2** do roadmap. Lugar certo: campo `since: datetime` no `GithubReleasesConfig`, valor na entry do `schedules.yaml`. O invariante 4 nem é implicado — cada tick do cron instancia flow NOVO com snapshot novo; não há flow em andamento a proteger.

### C2 — A watch list é um feed: a analogia com o `FeedWorker` basta, sem ginástica
`feed_url` é config, os itens do feed são dado. `github-watch` (a integração) é config, os repos inscritos são dado. Mesmo desenho que já existe em `kubo/workers/feed.py`. Não inventar defesa nova.

### C3 (CRÍTICA) — Lista vazia é ERRO, nunca run limpo
O D51 registra: sem escopo `notifications`, `/user/subscriptions` devolve `[]` **sem erro** — a falha silenciosa que quase produziu a conclusão errada na verificação. Se o worker não tratar `subscriptions == []` como `ErrorInfo(kind="config")`, ele **herda por construção a armadilha que a gente acabou de documentar**. Um dono com 260 watches nunca tem legitimamente zero. Além disso: `repos_seen` nos stats, para o encolhimento PARCIAL (regressão Custom, D51) ficar visível no card. **Enumerar como PRINCÍPIO no enunciado** (lição ADR-0021 §XII: o implementador segue a letra).

### C4 (CRÍTICA) — Paginar `/user/subscriptions`
260 repos; o default da API é 30/página. O `github_releases` hoje deliberadamente NÃO pagina *releases* (decisão v1, coberta pelo upsert idempotente) — copiar esse idioma aqui faz o worker ler **30 repos e parar, sem erro**. Paginação obrigatória: `per_page=100` + seguir `Link: rel="next"`, com **teto explícito de páginas (~10)** como guarda. É o bug mais provável da sessão.

### C5 — `published_at`: filtrar e GRAVAR são duas obrigações distintas
Cravar as duas no enunciado (ADR-0021 §XII: o agente/implementer segue a letra, não generaliza princípio). Edge decidido: release não-draft **sem `published_at`** → **skip + contador nos stats** (sem data não passa no filtro, e silêncio aqui seria a próxima falha silenciosa). Comparação sempre timezone-aware (a API devolve ISO-8601 UTC).

### C6 — CORTAR o estado `distilling` do template pipeline
O runner é **síncrono** (`flow_runner.py`: bloqueia até entregar; sem fila/poll). `distilling` só poderia significar (i) distiller inline no behavior — dois `run_worker` num flow, `set_task_run` liga UMA run por task (pergunta de schema não orçada) + pergunta de elenco (qual persona assina task mecânica sem LLM?), 2-3h; ou (ii) card estaciona esperando alguém movê-lo = evento/poll = **orquestrador, §1.2, proibido**. **Pipeline v1 = `queued → collecting → stored | failed`.** A destilação continua no cron das 09:00 — mecanismo existente, idempotente, provado. Uma task, uma run, zero mudança de schema. Desvio da spec §3.3 nomeado no ADR com gatilho de reabertura: *"distiller entra no flow quando houver consumidor de card por-estágio"*.

## Cortes de escopo (com motivo, não por timebox)

- **`flow_event` NÃO entra.** Não tem consumidor: o `github-releases` **já foi promovido** na 0018 — não há evento de promoção a consumir. E "agendamento automático" é impossível por construção: agendar = entry no `schedules.yaml`, arquivo no repo, deployado por PR (ADR-0010 §I); `_promote_dev` não pode escrevê-lo sem criar uma 4ª porta de escrita que edita config versionada em runtime. O que "a promoção passa a instanciar de verdade" significa honestamente: **promoção → dono adiciona a entry no `schedules.yaml` (PR, gitops) → cron instancia flows**. Volta quando o PRÓXIMO worker de agente for promovido e houver decisão sobre a aresta de proveniência `flow_dev -[produces]-> flow_pipeline` (que também pode ser gravada no clique da promoção, sem "trigger" novo). **`flow_event` NÃO entra no enum de `triggers` do template** — trigger declarado sem mecanismo é o campo mentiroso que o ADR-0016 §VIII proíbe.
- **Feeds/distiller/digest existentes NÃO viram flows.** Migrar tudo é escopo explosivo e encheria o board de 8 cards/dia. O `schedules.yaml` passa a ter DOIS tipos de entry (discriminador `worker:` vs entry de flow) — decisão nomeada no ADR.
- **21.5 (card no board) é CONDICIONAL:** verificar ANTES de prometer se o board renderiza flow/task genéricos. Renderiza → grátis. Exige view nova → corta, o flow aparece cru, polish em follow-up.

## Marcos

| # | Marco | PR |
|---|---|---|
| 21.0 | **Fix do 403≠rate-limit** (D55): distinguir rate limit real por `x-ratelimit-remaining: 0`/`retry-after`; 403 sem isso = `kind="http"`. Fecha D51 #1. ~30min, TDD. | `fix/*` |
| 21.1 | **Worker lê a watch list** (TDD/respx): `/user/subscriptions` **paginado** (C4) → `since` filtra por `published_at` (C1/C5) → `published_at` no metadata (C5) → **vazio = `ErrorInfo(kind="config")`** (C3) → `repos_seen`/`skipped_no_date` nos stats. Bump manifest **0.2.0**. **Deadline total** no run (o `FeedWorker` tem `_TOTAL_DEADLINE`; este só tem timeout por request — 260 GETs sequenciais × 15s tem pior caso de dezenas de minutos num `BlockingScheduler` que segura os outros jobs). | PR2 |
| 21.2 | **Integração `github-watch`** + runbook do PAT. Runbook diz a verdade: **PAT CLÁSSICO obrigatório** (fine-grained NÃO cobre `/user/subscriptions` — registrar para ninguém "melhorar" depois) e o escopo `notifications` **NÃO é read-only** (permite marcar como lido e inscrever/desinscrever threads — escrita de baixo risco, mas não chamar o token de "leitura"). | PR2 |
| 21.3 | **Template `pipeline`** (`queued → collecting → stored\|failed`, C6) + behavior no FLOW_REGISTRY + runner. | PR3 |
| 21.4 | **Scheduler:** cron instancia o flow (`run_flow` no corpo do job, não `run_worker` — mudança material do ADR-0010 §V). Entry no `schedules.yaml` **fora do trem 08:00-09:30**. **D56:** `max_items` do distiller 20 → 50. | PR3 |
| 21.5 | **Card no board** — CONDICIONAL, verificar antes (ver Cortes). | PR3 |
| 21.6 | **ADR-0022** (estende 0010 e 0016): desvios nomeados — sem `flow_event`, pipeline sem `distilling`, `schedules.yaml` com dois tipos de entry, `run_flow` no job, worker 0.2.0 mudando contrato de config de um worker JÁ promovido (o registro de promoção no grafo passa a descrever um contrato antigo — audit trail correto, nomear numa frase). **Volta ao advisor antes de cravar** (CLAUDE.md). | PR3 |
| 21.7 | **Smoke físico (gated no "pode executar"):** `since` = a data do dia, cron dispara, 260 repos, card `stored`, releases no grafo, digest do dia seguinte traz release destilada. | — |

## Critérios de aceite

- Worker: pagina 260 repos (teste com >100 provando a paginação, C4); `[]` vira `ErrorInfo(kind="config")` (teste negativo, C3); `since` filtra (teste com release antes/depois); `published_at` no metadata (asserção explícita, C5); sem `published_at` → skip contado.
- Pipeline: cron instancia flow; card chega em `stored`; falha do worker leva a `failed` com o erro visível.
- Estreia com `since` = hoje coleta **zero** (D52 provado, não presumido).
- ADR-0022 cravado com o advisor; suite verde; cobertura ≥85% em store/contracts/runtime.

## Escopo negativo

Sem `flow_event`; sem estado `distilling` no pipeline; sem migrar feeds/distiller/digest para flows; sem watermark/estado novo no worker; sem paginar *releases* (a v1 do worker segue com `per_page=30`, coberto pelo upsert); sem retry/fila/claim (§1.2 — o gatilho de orquestrador não dispara aqui); sem UI nova além do card condicional; sem tocar o rito de promoção; sem resolver a dívida restante do D51 (teste de streaming, nit do validador) — só o 403 (D55).

## Sacrifícios pré-declarados (ordem)

1. **21.5** (card no board) → o flow aparece cru; polish em follow-up.
2. **21.3/21.4/21.6** (template + scheduler de flow + ADR) → vira **sessão 0021b**.
   **Ponto de corte pré-acordado (hora ~4,5):** se o worker não estiver verde, a sessão entrega **21.0 + 21.1 + 21.2 + uma entry `worker:` COMUM no `schedules.yaml`** — o mecanismo ADR-0010 de hoje, zero flow. **Isto já entrega o valor de ponta a ponta:** as releases dos 260 repos entram no grafo diariamente e o dono lê amanhã. A arquitetura de cards espera uma semana sem custo nenhum.
**Nunca cortar:** C3 (vazio = erro), C4 (paginação), 21.0 (o fix do 403 — sem ele a estreia mente sobre a própria falha).

## Pontos de consulta ao advisor

1. Antes de fixar a forma da entry de flow no `schedules.yaml` (o discriminador é decisão de contrato).
2. ADR-0022 redigido, antes de cravar (obrigatório — CLAUDE.md).
3. Extraordinária: se o board NÃO renderizar flow genérico e a tentação for "só uma view rapidinha".

## Preparos do dono (runbook literal na sessão)

- **Criar o PAT clássico** com escopo `notifications` e pôr `GITHUB_TOKEN_WATCH` no `.env` do kubo-test **à mão** (o agente nunca lê nem escreve o `.env`; segredo não passa pelo chat — disciplina das sessões anteriores).
- Decidir o horário do cron (fora de 08:00-09:30, onde já roda o trem de feeds + distiller + digest).

## Dívidas e consequências nomeadas (não resolver aqui)

- **Curadoria dos 260 não foi re-validada.** Os 196 fork-parents entraram por união automática, e o roadmap registra "entulho visível" na lista. O `since` neutraliza a enxurrada de estreia, mas o custo real nomeado no D51 — **tempo de leitura do dono** — depende dos 260 serem curadoria de verdade. Medir na primeira semana; poda = `unwatch` (o produto funcionando como desenhado, não manutenção).
- **Acúmulo de cards:** um flow pipeline por dia, para sempre. Sem retenção/filtro, `/flows` vira lista infinita de cards `stored` idênticos em ~2 meses. Item de fila (UI, não arquitetura).
- **Refetch eterno:** `since` congelado + `per_page=30` = 260 GETs + ~7.800 upserts por rodada, para sempre. Verificado pelo advisor: `upsert_item` (chave `source|external_id`) **não** recoloca o item no funil de destilação (`items_without_distilled` filtra por ausência da aresta `derived_from`, não por timestamp) — o custo é HTTP + escrita local, **não LLM**. Rate limit: 260 req/rodada contra 5.000/h = folga de 19x. Aceito. Propriedade de brinde: `since` congelado **recupera outage** (scheduler parado 10 dias não perde nada; janela rolante perderia). Upsert é last-wins em `content` → release editada é re-capturada de graça.
- **Correção pendente no roadmap** (achado do advisor): o parágrafo *"Tarefa do dono antes da 0021: estrelar os repos, não migrar os 136"* ficou **órfão e mentindo** — a migração aconteceu e a lista é 260. Corrigir no PR de docs; em 6 meses parece tarefa pendente.
