# ADR-0018 — Boards + gates + caminho de escrita da UI: o gate humano no browser

> Status: proposto · Data: 2026-07-14 · valida o advisor (Fable 5) antes de cravar (marco 15.8)

## Contexto

A fase 2 fez o Trabalho nascer (ADR-0016): `kubo flow run analysis` instancia um
flow, a analista executa e o relatório vira `deliverable` no grafo, entregue no
Telegram. Faltava o outro lado do D14 — **"gate nunca é decisão de um clique"**:
o dono lê o resultado com contexto e **aprova/rejeita com motivo** antes da
entrega. A spec (§3.1/§3.2) modela isso como **gate = decisão humana numa
transição do board**, materializada como uma **segunda task** atribuída ao
Humano. Esta sessão traz o mecanismo ponta a ponta, **pelo browser**, sem
terminal.

**Alinhamento de escopo (crítico):** este NÃO é o gate do invariante 5 (promoção
de código gerado a pipeline operacional, fase 4). É a **primeira materialização
do mecanismo** que aquele gate usará. Nenhum bypass, nenhuma flag, nenhum
"auto-approve" de conveniência — nem em dev (o invariante 5 proíbe o bypass como
construção; construir o atalho aqui o normalizaria lá).

Este ADR **estende e emenda** dois anteriores e os nomeia:

- **ADR-0016** (flow mínimo): o gatilho de reversão da tabela `board_state`
  dizia "estado ganhar config (flag de gate)". A spec §3.1 modela gate como
  **transição**, não como estado — o gatilho é reescrito (§II). E `dispatch`
  ganha um terceiro `artifact` (`gate`) para a notificação (§III).
- **ADR-0014** (UI foundation): o tripwire R4 ("1ª rota de escrita → credencial
  separada ou path dedicado") dispara **como desenhado**. A escrita da UI ganha
  a credencial `kubo_rw` EDITOR, por-request, só nos handlers de escrita (§I).

Nenhuma tabela extra-spec nova. `flow`/`task`/`persona`/`deliverable` (spec §2.3)
já existem; os campos de decisão moram na task do gate (§IV) — a contenção do
ADR-0002 (terceira tabela extra-spec = `dispatch`) permanece intacta.

## Decisão

### I. E1 — Escrita da UI: credencial `kubo_rw` (ROOT-level EDITOR), por-request, só nos handlers de escrita

O tripwire do ADR-0014 R4 disparando conforme desenhado. Forma exata:

- **`DEFINE USER kubo_rw ON ROOT ... ROLES EDITOR`** — lê/escreve DADOS, **não
  gerencia usuários** (EDITOR não escala privilégio para OWNER; não cria/dropa
  usuários nem define permissões). Mesma forma de signin do root/`kubo_ro` →
  **zero branch no `client.py`** (Path A, precedente ADR-0014): um seam novo que
  lê env próprio e reusa `connect`.
- **Seam `connect_rw()`** chamado **exclusivamente dentro dos handlers POST de
  escrita** (as 2 ações do D38), conexão por-request, **nunca em app state**.
  Fail-fast: `KUBO_RW_SURREAL_PASS` ausente → só essas rotas dão **503**; o resto
  da UI (que lê por `kubo_ro`) continua vivo.
- Criação **one-time via runbook** (irmão do §2c do `kubo_ro`), senha por env,
  rotação idêntica ao `kubo_ro`.
- **O teste mais importante da sessão (footgun do no-op silencioso):** se um
  handler de escrita usar por bug a conexão `kubo_ro`, a escrita falha **em
  silêncio** e o gate "aprova" sem transicionar. Teste de integração obrigatório:
  aprova via rota real → **lê de volta** o estado + os campos de decisão. Sem
  esse teste, nada mergeia.

**Custo nomeado (consciente, não acidente de compose):** o `kubo-api` passa a
carregar, no env, GEMINI + key LLM + `TELEGRAM_BOT_TOKEN` + `destinations.yaml`
(a retomada pós-gate envia pelo Telegram — §V). Processo exposto a browser com
mais superfície. **Aceito** por Tailscale-only + auth + dono único.
**Pré-condição registrada:** exposição fora da tailnet reabre esta decisão
(junto com a pré-condição de TLS do ADR-0014).

### II. E2 — Gate é dado de TRANSIÇÃO no template; `board_state` NÃO entra

O template declara `gates: <subconjunto marcado de transitions>` — enumera o
FATO "este par exige decisão humana". O que "aprovar" FAZ é código do
`FLOW_REGISTRY` (§V), nunca declarado no YAML (passa a lista negativa do ADR-0016
§I: dado, não DSL). Forma:

```yaml
board:
  states: [created, analyzing, awaiting_review, delivered, rejected, failed]
  transitions:
    - [created, analyzing]
    - [analyzing, awaiting_review]
    - [awaiting_review, delivered]
    - [awaiting_review, rejected]
    - [analyzing, failed]
gates:
  - [awaiting_review, delivered]
  - [awaiting_review, rejected]
```

- **Loader:** `gates` é campo do `Board` com **default vazio** (`extra="forbid"`
  no nível). Validação: `gates ⊆ transitions` (par de gate fora das transições é
  config quebrada). **Default vazio é obrigatório por compatibilidade:** flows
  `analysis` já persistidos têm snapshot **sem** a chave `gates`; um campo
  required quebraria a re-hidratação do snapshot antigo.
- **Ambos os pares gated, não só o de entrega.** A rejeição também é decisão do
  gate ("motivo obrigatório na rejeição" é item nunca cortável). Se
  `[awaiting_review, rejected]` não estivesse em `gates`, a guarda da store não
  protegeria o reject e o enforcement viveria só na UI — o buraco que o E2 existe
  para fechar.
- **Enforcement (guarda de recusa incondicional no `transition_task`):** par ∈
  `snapshot.board.gates` → `StateError("transição de gate exige decisão — use
  decide_gate")`. **Sempre.** `transition_task` **nunca atravessa um gate** — não
  tem como portar contexto de decisão, então par gated ali é sempre erro. Lê do
  `flow.snapshot` (invariante 4), nunca do catálogo. Snapshot antigo sem `gates`
  → `[None]` → lista vazia → a guarda nunca dispara (regressão do `analysis`
  legado testada explicitamente).
- **Emenda ao gatilho do ADR-0016:** o texto dizia "flag de gate" imaginando
  gate-como-estado. Gate é **transição** (mais fiel à spec §3.1). Gatilho
  reescrito: **`board_state` entra quando um ESTADO ganhar config com consumidor
  real** (WIP limit renderizado, SLA por coluna). Hoje `board_state` seria o
  snapshot duplicado em records — dois leitores para um fato, sem consumidor.

### III. E3 (notificação) — `dispatch.artifact = "gate"`; watermark protegido por construção

- Notificação = **sender Telegram existente** + `insert_dispatch(artifact="gate")`
  no handler pós-worker (a task do gate acaba de nascer em `awaiting_review`).
- `DispatchPayload.artifact` vira `Literal["digest", "report", "gate"]`; o
  validador estende: `gate ⇒ watermark None` (gate não tem marca-d'água de
  acervo). **Sem migration:** o schema de `dispatch.artifact` é `string` livre
  (sem ASSERT enum) — o `Literal` é o vocabulário do contrato, a coluna aceita.
- **Nenhum worker emite `artifact="gate"`.** A notificação de gate é efeito
  colateral do RUNTIME via `insert_dispatch` direto (não passa por `RunResult`).
  O `Literal` fica como vocabulário único do campo — registrado aqui para que
  ninguém procure em 6 meses o "worker que produz gates".
- **Watermark do digest protegido por construção:** `last_dispatch_watermark`
  filtra `artifact = 'digest'` (E1/ADR-0016 §V) — um dispatch de `gate` **não
  move** o watermark. **Com teste confirmando** (dispatch de gate → o digest de
  amanhã não pula destilados).
- **Falha de notificação NÃO falha o gate** (best-effort nomeado): log
  estruturado, o gate segue visível no board da UI. O Telegram é conveniência de
  aviso; o board é a fonte da verdade.

### IV. E3 (modelagem) — Gate = SEGUNDA task, do Humano, usando os estados do board

- **Task do gate criada automaticamente na transição para `awaiting_review`**,
  `assigned_to → persona(humano)` — **quita o D33** (o Humano materializado
  finalmente recebe task). A task da analista também para em `awaiting_review`.
- **SEM segundo state machine** (`pending→approved` NÃO): a task do gate usa os
  estados do board. Na decisão, as **duas tasks transicionam juntas** para
  `delivered`/`rejected`. Campos de decisão (`decision`, `reason`, `decided_at`)
  **na task do gate** — registro novo seria 4ª tabela extra-spec (reabriria a
  contenção do ADR-0015; não há por quê).
- **Migration 0006:** `decision`/`reason`/`decided_at` como `option<...>` na
  `task` (SCHEMAFULL — tasks existentes e não-gate não podem violar o schema).
- **`decide_gate(...)` — a única porta sancionada, transacional.** A guarda de §II
  tranca `transition_task`; `decide_gate` é a porta com fechadura:
  `decide_gate(db, *, analyst_task, gate_task, to_state, decision, reason)` valida
  (ambas em `awaiting_review`, mesmo flow, par ∈ transitions ∩ gates,
  `reason` obrigatório quando `rejected`) e executa **uma** `run_transaction` com
  os dois UPDATEs + os campos de decisão na task do gate (risco 5 do advisor:
  crash no meio = board incoerente). **Divisão de papéis, não duplicação:** a
  guarda genérica é a tranca ("por aqui não passa"); `decide_gate` é a única
  travessia. Um helper privado `_snapshot_board` compartilha a leitura do snapshot.
- **UPDATE condicional e completo num statement:** `UPDATE $t SET state = $to (+
  decision/reason/decided_at na task do gate) WHERE state = 'awaiting_review'`.
  Assim uma corrida double-decide (duas abas vencendo a guarda de staleness)
  degrada para **no-op total**, nunca para decisão sobrescrita ou board
  incoerente. O pré-check + `StateError` continua antes para dar erro legível; o
  `WHERE` é o cinto de segurança de graça.

### V. E4 — Retomada pós-aprovação: SÍNCRONA no request, comportamento no `FLOW_REGISTRY`

- `FLOW_REGISTRY["analysis-review"]` vira um comportamento com **três entradas**
  keyed pelo nome (E4 do ADR-0016 preservado): **run-até-o-gate** (instancia,
  roda o worker, para em `awaiting_review`, cria a task do Humano, notifica),
  **resume-pós-gate** (aprovado) e **reject** (rejeitado). A **rota é casca**:
  auth + CSRF + validação de estado + chamada + render.
- **Approve:** `sendMessage` (~1s, via `kubo/distribution/telegram.py` —
  distribuição é BIBLIOTECA, não identidade de processo) → `insert_dispatch(
  artifact="report")` → `decide_gate(...)` das 2 tasks para `delivered` → 200.
  Rota `def` síncrona (threadpool, padrão ADR-0014 §4). **Sem `run`** — envio
  mecânico sem LLM; o dispatch audita o envio, a task do gate audita a decisão
  (um worker aqui seria cerimônia).
- **Reject:** sem envio; `decide_gate(...)` das 2 tasks para `rejected` +
  `reason` obrigatório → 200. Arquiva.
- **At-least-once herdado (ADR-0015):** crash entre o `sendMessage` e o dispatch/
  transação deixa o gate aberto; o dono clica de novo; pior caso = relatório
  duplicado para o dono. **Nomear, NÃO construir outbox.**
- **TOCTOU residual nomeado:** a guarda de staleness (§VI) + `Semaphore` + o
  `UPDATE ... WHERE` + dono único cobrem a corrida; o TOCTOU entre pré-check e
  transação é irmão do at-least-once (degrada para no-op, não para incoerência).

### VI. E5 — Disparo pela UI + endurecimento dos handlers

- **Disparo (~10-30s):** rota `def` no threadpool (não congela a UI nem
  `/healthz` com 1 worker; sem proxy → sem timeout intermediário). Amortecedores:
  (1) spinner + `hx-disabled-elt`; (2) `Semaphore(1)` não-bloqueante → **429**
  "já há flow em execução" (padrão do login); (3) crash deixa a task em
  `analyzing` = regime órfão do ADR-0016 §VII, **agora visível no board**.
- **Kanban fiel ao mockup real (`FlowsScreen.jsx`):** lista de flows (nome, badge
  template, badge gate, status, glifos do elenco) → clicar abre o **board DO FLOW
  individual** (colunas = `snapshot.states`; **cards = TASKS do flow**). Board
  compartilhado entre templates NÃO existe no mockup. Card de gate: ring âmbar +
  "aguardando você" + botões abrindo o **GateSheet** (painel lateral: contexto +
  motivo obrigatório na rejeição — D14 pronto no mockup).
- **CSRF — synchronizer token na sessão** (itsdangerous já assina): `csrf =
  secrets.token_hex(16)` no dict da sessão no login; hidden input no form;
  comparação com `hmac.compare_digest`. ~10 linhas, zero dep, zero estado no
  servidor. SameSite=Lax segue a defesa primária. (Double-submit rejeitado:
  segundo cookie, modos de falha mais sutis, sem ganho.)
- **Guarda de staleness (mais valiosa que o CSRF):** o form leva task id + estado
  esperado; task fora de `awaiting_review` → **409** + re-render. Protege
  duplo-clique / duas abas — o risco real do dono único.
- **Texto plano no painel, NUNCA markdown→HTML:** `deliverable.content` é untrusted
  no consumo (ADR-0016 §II, escopo negativo do ADR-0014) — renderiza como TEXTO
  PLANO (`white-space: pre-wrap`), nunca `markdown→HTML`, nunca `|safe`. `reason`
  é input renderizado: cap de tamanho na borda pydantic, autoescape do Jinja.

## Consequências

- **Positivo:** o ciclo completo sem terminal (disparar → gate → notificar →
  aprovar/rejeitar com motivo). O gate humano da spec §3.1/§3.2 existe de
  verdade; o Humano recebe task (D33 quitado). Nenhuma tabela extra-spec nova;
  decisão registrada no grafo com transação atômica.
- **Trade-off aceito:** o `kubo-api` ganha superfície (segredos LLM/Telegram no
  env — §I); retomada síncrona (crash → gate reaberto, at-least-once, sem
  outbox); `kubo_rw` é a 2ª credencial ROOT (rotação dobrada, EDITOR limita o
  dano). TOCTOU residual degrada para no-op, nomeado.
- **Contenção viva:** escrita da UI = exatamente 2 ações (D38); `transition_task`
  nunca atravessa gate; `board_state` e segundo state machine ficaram de fora com
  gatilho nomeado.
- **Gatilhos registrados:** (a) estado com config/consumidor real → `board_state`
  tabela (emenda ao ADR-0016); (b) segundo consumidor de "executar fora do
  request" (flow agendado com gate, retry automático, executor `cli` de minutos)
  → o desenho síncrono morre, vira "processo executor", ADR próprio + dono na
  mesa; (c) exposição fora da tailnet → reabre §I + TLS do ADR-0014.

## Alternativas rejeitadas

- **(ii) "Gravar intenção para outro processo consumir"** — rejeitada: é fila com
  outro nome (polling/claim/janitor, escopo negativo §1.2); e `kubo_ro` nem
  escreveria a intenção.
- **(iii) Endpoint HTTP interno no scheduler** — rejeitada: servidor dentro de
  `BlockingScheduler` + RPC interno + acoplamento da decisão à saúde do container
  = barroquismo.
- **`board_state` como tabela agora** — rejeitada: gate é transição, não estado;
  `board_state` seria snapshot duplicado sem consumidor (gatilho reescrito).
- **Segundo state machine para o gate (`pending→approved`)** — rejeitada: a task
  do gate usa os estados do board; máquina paralela duplicaria estado.
- **Registro novo de decisão (4ª tabela extra-spec)** — rejeitada: campos na task
  do gate cobrem; reabriria a contenção do ADR-0015.
- **Contexto de decisão opcional no `transition_task`** — rejeitada: `decide_gate`
  transacional elimina a tensão "a analista de carona precisa de contexto?"; a
  guarda genérica vira recusa incondicional (tranca), a decisão mora só onde E3
  mandou (task do gate).
- **`run` (worker) na retomada pós-gate** — rejeitada: envio mecânico sem LLM; o
  dispatch audita o envio, a task do gate audita a decisão — worker seria cerimônia.
- **Double-submit cookie para CSRF** — rejeitada: segundo cookie, modos de falha
  mais sutis, sem ganho sobre o synchronizer token na sessão já assinada.
