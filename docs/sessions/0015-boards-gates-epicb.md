# Sessão 0015 — Boards + Gates + EPIC-B: o Kubo no browser

> **Status:** aprovado pelo dono (2026-07-14, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5) — toca auth/credencial de banco, caminho de escrita da UI e mecânica de gate (invariante 5 em embrião)
> **Timebox:** 8 horas efetivas — factível porque a máquina existe (runner/store/dispatch/auth); risco de estouro é o marco de UI. Corte pré-acordado: estourou → **0015b pré-autorizada** (o que sobrar dos marcos)
> **Estrutura:** 1 PR — branch `feat/0015-boards-gates` (D16)
> **Regra do período:** NENHUM deploy enquanto o dreno do backlog estiver rodando no kubo-test.
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

O ciclo completo sem terminal: disparar análise **pelo browser** → relatório PARA no gate → notificação no Telegram → abrir o **board kanban**, ler com contexto e **aprovar/rejeitar com motivo** → aprovado entrega, rejeitado arquiva. D14 ("gate nunca é decisão de um clique") e o mecanismo de gate da spec §3.1/§3.2 virando código. **Nota de alinhamento (advisor):** este NÃO é o gate do invariante 5 (promoção de código, fase 4) — é a primeira materialização do mecanismo que aquele gate usará. **Nenhum bypass, nenhuma flag, nem "auto-approve" de conveniência em dev.**

## Decisões do dono

- **D37:** primeiro flow com gate = **`analysis-review`** (variante: relatório para no gate antes do envio; aprovado→entrega, rejeitado→arquiva com motivo). Template `analysis` original continua existindo sem gate.
- **D38:** escrita da UI = **exatamente 2 ações**: (a) aprovar/rejeitar gate com motivo (painel com contexto — D14); (b) disparar flow analysis/analysis-review (form com pergunta).

## Decisões fixadas pela consulta ao advisor (GO com E1–E5)

### E1 — Escrita da UI: credencial `kubo_rw` (ROOT-level EDITOR), por request, só nos 2 handlers
O tripwire do ADR-0014 R4 disparando **como desenhado** ("1ª rota de escrita → credencial separada ou path dedicado"). Forma exata:
- `DEFINE USER kubo_rw ON ROOT ... ROLES EDITOR` — lê/escreve dados, **não gerencia usuários** (não escala privilégio). Mesma forma de signin do root → **zero branch no `client.py`** (Path A, precedente ADR-0014).
- Seam `connect_rw()` chamado **exclusivamente dentro dos 2 handlers POST**, conexão por request, nunca em app state. Fail-fast: `KUBO_RW_SURREAL_PASS` ausente → as 2 rotas dão 503, resto da UI (kubo_ro) vive.
- Criação one-time via runbook (irmão do §2c), senha por env, rotação idêntica ao kubo_ro.
- **Rejeitados como escopo negativo no ADR-0018:** (ii) "gravar intenção pra outro processo consumir" = fila com outro nome (polling/claim/janitor — §1.2; e kubo_ro nem escreveria a intenção); (iii) endpoint HTTP interno no scheduler = servidor dentro de BlockingScheduler + RPC interno + acoplamento da decisão à saúde do container — barroquismo.
- **Custo nomeado (consciente, não acidente de compose):** kubo-api passa a carregar GEMINI + key LLM + TELEGRAM_BOT_TOKEN + destinations.yaml no env — processo exposto a browser com mais superfície. Aceito por Tailscale-only + auth + dono único; **pré-condição registrada:** exposição fora da tailnet reabre (junto com a pré-condição TLS do ADR-0014).

### E2 — Gate é dado de TRANSIÇÃO no template; board_state NÃO entra
- Template declara `gates: [[awaiting_review, delivered]]` — subconjunto marcado de `transitions`. Passa a lista negativa: enumera FATO ("este par exige decisão humana"); o que "aprovar" faz é código do FLOW_REGISTRY. Loader valida `gates ⊆ transitions`, `extra="forbid"`; runtime valida contra `flow.snapshot` (invariante 4).
- Enforcement mínimo: `transition_task` ganha assert (~5 linhas, espelho do R6): par ∈ `snapshot.gates` exige contexto de decisão, senão `StateError`.
- **ADR-0018 EMENDA o gatilho do ADR-0016 nomeadamente** (o texto dizia "flag de gate" imaginando gate-como-estado; a spec §3.1 modela gate-como-transição — mais fiel). Gatilho reescrito: `board_state` entra quando um ESTADO ganhar config com consumidor real (WIP limit renderizado, SLA por coluna). `board_state` agora seria o snapshot duplicado em records — dois leitores para um fato, sem consumidor.

### E3 — Gate = SEGUNDA task, do Humano (spec §3.2 literal), usando os estados do board
- Task do gate criada automaticamente na transição para `awaiting_review`, `assigned_to → persona(humano)` — **quita o D33** (humano materializado finalmente recebe task).
- **SEM segundo state machine** (`pending→approved` NÃO): a task do gate usa os estados do board; na decisão, transiciona **junto** com a task da analista para `delivered`/`rejected`. Campos de decisão (`decision`, `reason`, `decided_at`) **na task do gate** — registro novo seria 4ª tabela extra-spec (contenção do ADR-0015 reabriria planejamento; não há por quê).
- **As duas tasks transicionam NUMA transação da store** (risco 5 do advisor: crash no meio = board incoerente).
- Notificação: sender Telegram existente + `insert_dispatch(artifact="gate")` no handler pós-worker. Watermark protegido por construção (filtro positivo `artifact='digest'` — E1/0013), **com teste confirmando** (dispatch de gate não move watermark; gate → watermark `None` no validador). **Falha de notificação NÃO falha o gate** (best-effort nomeado: log estruturado, gate segue visível na UI).

### E4 — Retomada pós-aprovação: SÍNCRONA no request, comportamento no RUNTIME
- A rota **não implementa** o pós-gate — chama função do runtime: `FLOW_REGISTRY["analysis-review"]` vira par **(run-até-o-gate, resume-pós-gate)** — comportamento keyed pelo nome, E4 do ADR-0016 preservado. Rota é casca: auth + CSRF + validação de estado + chamada + render.
- Distribuição é BIBLIOTECA (`kubo/distribution/telegram.py`), não identidade de processo — kubo-api importá-la não é acoplamento errado.
- Approve: sendMessage (~1s) → `insert_dispatch(artifact="report")` → transação das 2 tasks → 200. Rota `def` síncrona (threadpool — padrão ADR-0014 §4). **Sem `run`** — envio mecânico sem LLM; dispatch audita o envio, task do gate audita a decisão (worker aqui seria cerimônia).
- **At-least-once herdado (ADR-0015):** crash entre send e dispatch deixa gate aberto; dono clica de novo; pior caso = relatório duplicado pro dono. Nomear, NÃO construir outbox.
- **Gatilho registrado no ADR-0018:** segundo consumidor de "executar fora do request" (flow agendado com gate, retry automático, executor cli de minutos na fase 3) → o desenho síncrono morre e a conversa vira "processo executor", ADR próprio + dono na mesa. Não antecipar.

### E5 — Disparo pela UI: síncrono com 3 amortecedores; kanban conforme o mockup REAL
- Disparo (~10-30s): rota `def` no threadpool (~40 threads — não congela a UI nem /healthz com 1 worker); sem proxy na topologia → sem timeout intermediário. Amortecedores: (1) spinner + `hx-disabled-elt`; (2) `Semaphore(1)` não-bloqueante → 429 "já há flow em execução" (padrão do login); (3) crash deixa task em `analyzing` = regime órfão do ADR-0016 §VII — **agora visível no board** (bônus da sessão).
- **Kanban (correção de desvio no draft):** `FlowsScreen.jsx` é **lista de flows** (nome, badge template, badge gate, status, glifos do cast) → clicar abre o **board DO FLOW individual** (colunas = `snapshot.states`; **cards = TASKS do flow, não flows**). Board compartilhado entre templates NÃO existe no mockup — problema de união de colunas não existe. Card de gate: ring âmbar + "aguardando você" + botões abrindo o **GateSheet** (painel lateral com contexto + motivo obrigatório na rejeição — D14 pronto no mockup).
- Desvios pré-declarados na tabela de paridade: budget (ADR-0016 §VIII — não existe), link de PR no gate (não existe), "Retomar flow"/pausado (não existe), tasksOpen; "Novo fluxo" = form D38-b (se sobreviver ao corte).

### CSRF e endurecimento dos handlers
- **Synchronizer token na sessão** (itsdangerous já assina): `csrf = secrets.token_hex(16)` no dict da sessão no login; hidden input no form; comparação com `hmac.compare_digest`. ~10 linhas, zero dep, zero estado no servidor. (Double-submit rejeitado: segundo cookie, modos de falha mais sutis, sem ganho.) SameSite=Lax segue sendo a defesa primária.
- **Guarda de staleness (mais valiosa que o CSRF):** form leva task id + estado esperado; task fora de `awaiting_review` → 409 + re-render. Protege duplo-clique/duas abas — o risco real do dono único.
- **Painel de gate renderiza `deliverable.content` como TEXTO PLANO (`pre-wrap`), NUNCA markdown→HTML** (untrusted no consumo — ADR-0016 §II; escopo negativo do ADR-0014). Invariante da tela, não descoberta de PR. `reason` é input renderizado: cap de tamanho na borda pydantic, autoescape, nunca `|safe`.

## Marcos (ordem do advisor: decisão → dado → comportamento → UI)

| # | Marco |
|---|---|
| 15.1 | **ADR-0018 esqueleto** (E1–E5 cravados; emenda ao gatilho do ADR-0016; escopos negativos ii/iii; gatilhos futuros) |
| 15.2 | **Template `analysis-review`** (`created→analyzing→awaiting_review→delivered\|rejected\|failed`) + `gates:` no loader/snapshot (TDD) |
| 15.3 | **Store:** campos de decisão na task, guarda de gate no `transition_task`, transação das 2 tasks, `artifact="gate"` (+ teste watermark) |
| 15.4 | **Runtime:** split do handler (run-até-gate / resume / reject) + criação da task do Humano na transição + notificação best-effort |
| 15.5 | **Credencial `kubo_rw`** (runbook one-time + compose env do kubo-api + `connect_rw()` + fail-fast 503) |
| 15.6 | **UI:** lista Fluxos → board do flow (cards=tasks, gate em âmbar) → GateSheet (contexto completo, motivo obrigatório) → 2 POSTs com CSRF + staleness → form de disparo (se houver fôlego) |
| 15.7 | **Deploy `./scripts/deploy.sh` (SÓ com dreno parado) + smoke físico gated:** ciclo completo no browser — disparar → notificação → board → aprovar → relatório no Telegram; e o caminho de rejeição com motivo |
| 15.8 | **ADR-0018 final (advisor valida antes de cravar)** + notas + tabelas de paridade conferidas |

## O teste mais importante da sessão (risco 1 do advisor)

**Footgun do no-op silencioso:** se um handler de escrita usar por bug a conexão `kubo_ro`, a escrita falha EM SILÊNCIO e o gate "aprova" sem transicionar. Teste de integração obrigatório: aprova via rota real → **lê de volta** o estado + campos de decisão. Sem esse teste, nada mergeia.

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0018 antes de cravar.
2. **Extraordinária:** task do gate precisar de ciclo de vida que o board não expressa (reabre modelagem E3); qualquer tentação de fila/outbox/executor; kubo_rw se revelar insuficiente como EDITOR.
3. Conclusão da sessão.

## Tarefas do dono

- Rodar o one-time do `kubo_rw` no servidor quando a sessão pedir (senha via env — rito de sempre) + env no `.env`.
- **"Pode executar"** no deploy (15.7) — **somente com o dreno parado**.
- Ser o primeiro humano a aprovar (e rejeitar!) um gate do Kubo no browser — o smoke exige os dois caminhos.

## Ordem de sacrifício

1. **1º:** form de disparo D38-b (smoke dispara via `kubo flow run analysis-review` no CLI).
2. **2º:** toggle/busca na lista de Fluxos.
3. **NUNCA cortáveis:** motivo obrigatório na rejeição; decisão registrada no grafo (transação); CSRF + staleness; texto plano no painel; teste do no-op silencioso; tabelas de paridade (lista, board, GateSheet); ADR-0018 com emenda ao 0016.

## Critérios de aceite

- [ ] Ciclo completo no browser: disparo (UI ou CLI se cortado) → gate notificado no Telegram → board mostra card âmbar → GateSheet com relatório (texto plano) + fontes + flow → aprovar entrega no Telegram / rejeitar exige motivo e arquiva.
- [ ] Decisão no grafo: task do gate com decision/reason/decided_at; as 2 tasks transicionadas atomicamente.
- [ ] Teste do no-op silencioso verde (aprova via rota real, lê de volta).
- [ ] Dispatch de gate comprovadamente não move watermark do digest (teste).
- [ ] kubo_ro segue sendo o caminho default; `connect_rw` só nos 2 handlers (grep/teste); 503 sem a env.
- [ ] CSRF + staleness testados; `reason` capado e escapado.
- [ ] Paridade com FlowsScreen conferida (screenshots; desvios só os pré-declarados).
- [ ] Cobertura ≥85%; ADR-0018 mergeado (emendando 0014 e 0016); PR conforme; main verificado.
- [ ] Notas: fila da 0016 (executor cli + GitHub), gatilhos registrados.

## Escopo negativo da sessão

- Fila/outbox/polling/claim/executor-processo NÃO (§1.2 — gatilho registrado). Endpoint HTTP interno no scheduler NÃO. Intenção-gravada-para-consumo NÃO.
- board_state tabela NÃO (gatilho reescrito). Segundo state machine para gate NÃO. Registro novo de decisão NÃO (campos na task).
- Markdown→HTML no painel NUNCA. Auto-approve/bypass/flag de gate NUNCA (nem em dev).
- Escrita além das 2 ações do D38 NÃO. Re-rodar flow falho NÃO (registrado pra depois). E-mail NÃO.
- Deploy com dreno rodando NÃO. Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-14; decisões do dono D37–D38; consulta de validação ao advisor (Fable 5): GO com E1–E5 — kubo_rw EDITOR por-request como o tripwire do ADR-0014 disparando conforme desenhado (delegação e RPC interno rejeitados como fila/barroquismo), gate como dado de transição no snapshot (emenda ao gatilho do ADR-0016), gate = segunda task do Humano usando os estados do board com decisão em transação, retomada síncrona no request com comportamento no FLOW_REGISTRY (par run/resume), CSRF synchronizer + guarda de staleness, kanban fiel ao mockup real (lista→board por flow, cards=tasks), teste do no-op silencioso como o mais importante da sessão.*
