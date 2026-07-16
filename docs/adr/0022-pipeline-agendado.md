# ADR-0022 — Pipeline agendado: watch list, flow por cron, agendamento de flow

> Status: **aceito** (GO com correções, validado pelo advisor Fable 5, 2026-07-16) · Data: 2026-07-16

## Contexto

A sessão 0021 (`docs/sessions/0021-pipeline-agendado.md`) fecha o laço coletar→destilar→distribuir
com curadoria real: o worker `github-releases` (promovido na sessão 0018, ADR-0021) passa a ler a
watch list nativa do dono no GitHub (260 repos) em vez de uma lista estática em config, e um cron
diário instancia um FLOW — não um `run_worker` solto — pra registrar a coleta como um card no
board, com proveniência de grafo (ADR-0016).

Isto introduz três coisas que não existiam antes e que a spec/ADRs anteriores não cobrem
diretamente:

1. Um worker JÁ PROMOVIDO (ADR-0021, sessão 0018b) muda de contrato de config numa versão minor
   (0.1.0 → 0.2.0) — o registro de promoção no grafo passa a descrever um contrato que não existe
   mais.
2. O `schedules.yaml` (ADR-0010) precisa agendar um FLOW, não só um worker — algo que o desenho
   original do agendamento (`ScheduleEntry{worker, cron, config}` → `run_worker`) não previa.
3. O flow runner (ADR-0016 §III) ganha seu primeiro consumidor NÃO-humano: até aqui, toda
   instanciação de flow partia de um humano autenticado (CLI ou browser). Um flow agendado por
   cron é uma classe de disparo nova.

Consultei o advisor (Fable 5) duas vezes nesta sessão antes de fixar a forma: uma vez sobre o
discriminador da entry em `schedules.yaml` e o canal de config `schedules.yaml → run_flow →
worker`, e este ADR passou pela terceira consulta (obrigatória, CLAUDE.md) antes de ser cravado —
as 4 correções que ela pediu já estão incorporadas no texto abaixo.

## Decisão

### D51-D56 (decisões do dono, já registradas no plano da sessão — resumo para o índice do ADR)

- **D51** — watch list nativa do GitHub (não lista mantida à mão), 260 repos.
- **D52** — sem backfill: `since` é o piso de estreia, congelado.
- **D53** — fatia vertical fina (pipeline + agendamento + worker), dívida do worker (D51 do
  roadmap) fica fora, exceto D55.
- **D54** — PAT dedicado (`GITHUB_TOKEN_WATCH`, integração `github-watch`), nunca alargar o token
  do rito de promoção.
- **D55** — fix do 403≠rate-limit sai em PR curto antes do smoke (`fix/github-releases-rate-limit`,
  #55).
- **D56** — `max_items` do distiller sobe de 20 para 50 no mesmo PR do agendamento.

### C1-C6 (correções do advisor no planejamento, já incorporadas no código — resumo)

`since` mora na config do worker, nunca no template (C1); watch list é um feed, mesma analogia do
`FeedWorker` (C2); lista vazia é `ErrorInfo(kind="config")`, nunca run limpo (C3, crítica);
`/user/subscriptions` pagina com teto de 10 páginas (C4, crítica); filtrar e gravar `published_at`
são duas obrigações separadas (C5); pipeline v1 corta o estado `distilling` (C6).

### Worker `github-releases` 0.1.0 → 0.2.0 é uma mudança de contrato, não uma migração

`GithubReleasesConfig.repos: list[str]` foi REMOVIDO; `since: datetime` (tz-aware obrigatório) é
o único campo novo. Nenhum outro arquivo do repo referenciava `repos=` fora do próprio worker e
seus testes — não há back-compat shim porque não há consumidor a proteger. A integração muda de
`github-releases` (`GITHUB_TOKEN_READONLY` compartilhado com o rito de promoção) para `github-watch`
(`GITHUB_TOKEN_WATCH` dedicado, D54). A integração `github-releases` ficou órfã (nenhum outro
consumidor) e foi REMOVIDA no mesmo PR — o git history é o audit trail do catálogo; deixar um
YAML de integração vivo com `secret_ref` ativo e zero consumidor é a superfície morta que confunde
least-privilege depois.

**Consequência nomeada:** o registro de promoção do worker no grafo (sessão 0018b, ADR-0021)
continua apontando para o manifest 0.1.0 — é audit trail correto (documenta o que foi promovido
NAQUELE momento), não um erro a corrigir. Um worker já promovido pode evoluir de contrato sem
reabrir o rito; o rito prova a PROMOÇÃO do código, não congela o schema para sempre. Isto NÃO é
licença geral: mudança subsequente a um worker promovido segue a porta pela qual entra — PR
humano normal (este caso, D51-D56 vieram de planejamento com o dono) — ou, se gerada por agente,
um novo flow `dev-kubo` com gate (invariante 5), como já provado fisicamente no achado D44
(sessão 0018b). O rito garante que código gerado por agente NUNCA vira operacional sem o gate;
não garante que um worker fique congelado depois de promovido.

### `schedules.yaml`: união `WorkerEntry | FlowEntry`, não um modelo único com validação cruzada

Consultado o advisor: união de dois modelos Pydantic (`extra="forbid"` nos dois, campos
obrigatórios disjuntos — `worker` vs `flow`+`question` — desambiguam sem `discriminator=`
explícito) em vez de um `ScheduleEntry` único com `worker: str | None` + `flow: str | None` e um
validator condicional. Razão: a entry de flow precisa de `question` (obrigatória, vira o `flow.
question` persistido e hoje também compõe o rótulo do card) — no modelo único isso vira uma
segunda camada de validação condicional ("`question` exigida sse `flow` presente"), exatamente a
sopa de regras que o union evita com dois modelos de 4 linhas cada.

### `run_flow` ganha `worker_config`, estendendo o contrato do ADR-0016 §III — não uma segunda porta

`run_flow(..., worker_config: Mapping[str, Any] | None = None)` é a única mudança ao contrato
existente: kwarg opcional, aditivo, ignorado pelos 4 behaviors pré-existentes (`analysis`,
`analysis-review`, `dev-mini`, `dev-kubo`), que ganharam o parâmetro morto só para o call site
único de `run_flow` não levantar `TypeError`. Alternativa considerada e rejeitada (opção B da
consulta ao advisor): uma função `run_scheduled_flow` dedicada, que NÃO passa por `run_flow`.
Rejeitada porque `run_worker` já é o "ÚNICO mecanismo de execução" declarado no docstring do
`flow_runner.py` (ADR-0016 §III) — uma segunda porta de entrada de flow duplicaria carga de
catálogo/registry e, em 6 meses, existiriam duas formas de rodar um flow que divergem em
silêncio. A prova de que a extensão é segura: os 4 behaviors existentes continuam cobertos pelos
testes verticais que passam POR `run_flow` (não chamam os `_run_*` privados direto) — um behavior
esquecido no novo kwarg quebraria em `TypeError` de runtime, não pego pelo pyright (`FlowBehavior.
run: Callable[..., FlowRunResult]` não checa kwargs), e a suíte prova que isso não aconteceu.

### Validação eager de `FlowEntry.config` via `FlowBehavior.config_model`

`FlowBehavior` ganha um campo opcional `config_model: type[BaseModel] | None = None` — `None` nos
4 behaviors existentes (nunca passam por `build_scheduler`), `GithubReleasesConfig` em `pipeline`.
`build_scheduler`/`_add_flow_job` valida `entry.config` contra ele no BOOT, mesmo padrão já usado
para `WorkerEntry` desde o ADR-0010: um `since` malformado falha no `docker compose up`, não às
06:00 do dia seguinte no primeiro disparo do cron.

### `triggers: [scheduled]` passa a ser ENFORÇADO, não decorativo

Achado do security-reviewer nesta sessão: `FlowTemplate.triggers` existe desde o ADR-0016 mas
nunca foi lido por código nenhum — todo template hoje declara `triggers: [manual]` (`analysis`,
`analysis-review`, `dev-mini`, `dev-kubo`) sem nada impedindo uma entry `{flow: dev-mini, ...}` de
ser agendada no cron por engano (typo, copy-paste). `dev-mini` abre um `CliExecutor` real, gasta
`budget_usd`, muta um clone do sandbox e abre PR — tudo sem humano, se alguém conseguisse agendar
por acidente. `_add_flow_job` agora rejeita (`ConfigError`) qualquer `FlowEntry` cujo template não
declare `"scheduled"` em `triggers`, ANTES de checar registry/cron/config. Isto fecha
retroativamente o "campo mentiroso" que o próprio plano da sessão citava (ADR-0016 §VIII) — não só
para `pipeline`, para os 4 templates humano-gated também, que agora têm uma garantia que não
tinham antes desta sessão.

**Escopo do enforcement:** só na FRONTEIRA do agendador (`_add_flow_job`, no boot do scheduler).
`run_flow` em si continua sem checar `triggers` — um humano autenticado ainda pode instanciar
`pipeline` manualmente via CLI/browser, e nada impede isso, por desenho: humano presente é
exatamente o caso que o invariante 5 (gate humano) já cobre. O risco fechado aqui é especificamente
o disparo DESATENDIDO por cron, não toda forma de disparo.

### `flow_event` NÃO entra no enum de `triggers`

Nenhum consumidor: `github-releases` já foi promovido (sessão 0018b), não há evento de promoção a
consumir. "Agendamento automático pós-promoção" honestamente significa: promoção → dono adiciona a
entry no `schedules.yaml` (PR, gitops) → cron instancia flows. A aresta de proveniência
`flow_dev -[produces]-> flow_pipeline` fica para quando o PRÓXIMO worker de agente for promovido —
pode ser gravada no clique da promoção, sem precisar de um trigger novo no template.

### Pipeline v1 sem estado `distilling`

O runner é síncrono (`flow_runner.py`, sem fila/poll) — `distilling` inline exigiria um segundo
`run_worker` na mesma task (`set_task_run` liga UMA run por task, sem schema para múltiplas) ou o
card parado esperando alguém movê-lo (evento/poll = orquestrador, escopo negativo §1.2, proibido).
Board v1: `queued → collecting → stored | failed`. A destilação continua no cron das 09:00,
mecanismo existente e idempotente. **Gatilho de reabertura:** distiller entra no flow quando
houver consumidor de card por-estágio.

### Persona `coletor` reusa `executor: human`

`create_task` exige uma persona (todo task tem dono no grafo); `GithubReleasesWorker` não usa
NENHUM executor (roda sem LLM). O enum `Executor` (`kubo/runtime/personas.py`) só tem
`api | cli | human` — estender para um 4º valor seria mudança de schema, e a fatia vertical fina
(D53) não orça isso. `coletor.yaml` reusa `executor: human` (não é uma pessoa — é "sem executor
construído"); `_build_executor` nunca é chamado para esta persona, e o `is_gate`/`persona_glyph`
da UI já discriminam por NOME de persona (`"humano"`), não por `executor`, então o reuso não
confunde nenhuma lógica existente (checado pelo security-reviewer). **Gatilho de reabertura:**
se um segundo caso mecânico aparecer, um valor dedicado do enum se justifica.

### `schedules.yaml` real ganha a entry do pipeline

`since` congelado no piso de estreia (D52), cron às 07:00 (fora do trem 08:00-09:30 de
feeds+destilador+digest). Comentário explícito no arquivo pedindo ao dono para ajustar `since`
para a data real do primeiro disparo antes do deploy — mesmo idioma já usado pra pedir
reconciliação das URLs de feed.

## Consequências

**Positivas:** o dono lê releases das ferramentas que acompanha sem visitar repo nenhum; o worker
já promovido ganha um consumidor real de produção pela primeira vez; `triggers` deixa de ser
decorativo para TODOS os templates, fechando uma lacuna de segurança que existia desde o
ADR-0016, não só para este pipeline.

**Negativas/trade-offs nomeados (dívidas aceitas, não resolvidas aqui):**
- Curadoria dos 260 repos não foi revalidada (196 fork-parents entraram por união automática) — o
  `since` neutraliza a enxurrada de estreia, mas o custo real (tempo de leitura do dono) depende
  de curadoria de verdade. Medir na 1ª semana; poda = `unwatch`.
- Acúmulo de cards: um flow pipeline por dia, para sempre, sem retenção — vira lista infinita em
  ~2 meses. Item de fila de UI, não de arquitetura.
- Refetch eterno: `since` congelado + `per_page=30` = 260 GETs + ~7.800 upserts por rodada, para
  sempre. Custo é HTTP+escrita, não LLM (`upsert_item` não recoloca no funil de destilação);
  rate limit tem folga de 19x. Aceito — e é o que dá a propriedade de recuperar outage de graça.
- Parágrafo órfão no roadmap ("estrelar os repos, não migrar os 136") mente sobre o estado atual
  (a migração aconteceu, a lista é 260) — corrigir no PR de docs desta sessão.

**Neutras/operacionais:** dois tipos de entry em `schedules.yaml` a partir de agora — quem editar
o arquivo precisa saber a diferença (`worker:` vs `flow:`); a validação eager no boot cobre o erro
mais provável (config malformada), mas o discriminador em si não tem enforcement de "só um dos
dois" além do que a união Pydantic já garante estruturalmente.

## Alternativas rejeitadas

- **`run_scheduled_flow` dedicado, sem passar por `run_flow`** — evitaria tocar os 4 behaviors
  existentes, mas cria uma segunda porta de execução de flow (viola ADR-0016 §III), rejeitada
  pelo advisor.
- **`ScheduleEntry` único com validação condicional `worker` xor `flow`** — mais compacto em
  linhas, mas empurra a complexidade pra dentro de um validator em vez de dois schemas simples;
  rejeitada pelo advisor.
- **Serializar `config` dentro de `question`** (JSON stringificado) — abuso de tipo, persiste
  lixo num campo que a UI mostra como pedido humano, cria superfície de injection num campo de
  texto livre. Descartada sem ambiguidade.
- **`distilling` inline no flow (dois `run_worker` numa task)** — exigiria schema novo (múltiplas
  runs por task) fora do orçamento da fatia fina (C6); cortada, com gatilho de reabertura nomeado.
- **Estender o enum `Executor` com um 4º valor mecânico** — mudança de schema que a fatia fina
  (D53) não cobre; a reutilização de `executor: human` é o desvio documentado em seu lugar.
- **Backfill completo na estreia** (260 repos × todas as releases históricas) — ~7.800
  destilações de estreia, inaceitável (D52).
