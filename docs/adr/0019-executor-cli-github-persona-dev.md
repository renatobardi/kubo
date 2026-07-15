# ADR-0019 — Executor `cli` + GitHub: a persona dev nasce (task → PR real → gate)

> Status: **proposto (rascunho)** · Data: 2026-07-14 · cravado no marco 16.9, após o
> spike (16.2) provar o mecanismo no kubo-test e o advisor (Fable 5) validar. Estende
> ADR-0013 (seam do executor), ADR-0016 (budget/runner fino, gatilho (c)), ADR-0018
> (gate/board no browser) e ADR-0009 (contrato de worker §VIII).

> **Nota de rascunho:** as **Perguntas abertas** ao final são respondidas pelo spike
> 16.2 e pela evidência do SDK pinado. Enquanto o status for `proposto`, nenhuma delas
> é decisão fechada — o esqueleto marca o eixo e o critério, não crava. As seções de
> **Decisão** abaixo são as que o plano 0016 (aprovado, advisor-validado nas correções
> C1–C4 e emendas E1–E8) já fixou independentemente do spike.

## Contexto

A fase 2 fez o Trabalho nascer (ADR-0016) e o gate humano no browser (ADR-0018). Esta
sessão fecha a **fase 3 da spec (§5)**: a **persona dev** com executor `cli` (Claude
Code via **Claude Agent SDK**, modelo Sonnet). O dono cria uma task dev → o agente
implementa num **clone efêmero de um repo sandbox** → o worker faz push e abre um **PR
real no GitHub** → o **gate humano** (mecânica ADR-0018, já existente) resume o resultado
e mostra o link do PR → **aprovar** grava a decisão e fecha o flow; **rejeitar** fecha o
PR via API com o motivo em comentário. Sem autonomia de criação: o dono cria tasks, os
agentes executam.

**Alinhamento de escopo (crítico):** esta é a **primeira fatia vertical** da fabricação
de código (spec §5), o esqueleto que a fase 4 (código gerado promovido a pipeline) reusa.
O **invariante 5** (gate humano na promoção, sem bypass) permanece intacto **e é honrado
por construção**: o Kubo não tem capacidade de merge (§IX); o gate está no PR, não no
turno do agente (§X). Nenhum bypass, nenhuma flag.

### Sumário das decisões do dono (D35–D38)

- **D35** — fatia vertical completa: task → PR real → gate. Esqueleto reusado pela fase 4.
- **D36** — modelo **Sonnet** na persona dev + **budget por flow** implementado nesta
  sessão; `ANTHROPIC_API_KEY` paga, via env (invariante 8).
- **D37** — repo **sandbox fixo privado**, criado manualmente pelo dono; **PAT
  fine-grained restrito a esse único repo** (`contents:write`, `pull_requests:write`). O
  side-effect "criar repo na instanciação" (spec §3.1) fica para quando um flow real
  precisar.
- **D38** — aprovar no gate = decisão no grafo + flow fechado. **O Kubo não faz merge**
  (anti-bypass por construção, padrão ADR-0018 §V-bis); o merge é clique do dono no
  GitHub. Rejeitar = fechar o PR via API com o `reason` em comentário.

Nenhuma tabela extra-spec nova. `flow`/`task`/`persona`/`deliverable` (spec §2.3) já
existem; o PR entra como `deliverable` com `kind=pr` (§VI).

## Decisão

### I. Seam do `CliExecutor` é SEPARADO do Protocol `Executor` — e a separação é fronteira de segurança

O Protocol `Executor` (`kubo/executors/base.py`) é single-shot **sem tools por
construção** (ADR-0013; ADR-0009 §VIII regra 1): `complete(instruction,
untrusted_content, response_model) -> T`. O `CliExecutor` tem contrato
**fundamentalmente diferente** — "prompt in → stream de eventos out" (subprocess agêntico
com filesystem/bash). Ele **NÃO implementa** `Executor.complete`; é um seam próprio, com
Protocol mínimo próprio (para fake em teste, espelho do ADR-0013), e o worker dev depende
desse seam, nunca do concreto.

A incompatibilidade dos contratos **é feature, não acidente**: se os dois satisfizessem
um Protocol comum, um worker do circuito de conteúdo coletado poderia receber, por engano
de wiring, um executor agêntico com filesystem e bash — exatamente a classe de acidente
que o sistema de tipos hoje impossibilita. O sistema de tipos é a fronteira.

**"Executor" no CLAUDE.md ("adapters sob a abstração `executor` (`api|cli`)") é conceito
de DOMÍNIO** — o campo `executor` da persona seleciona o caminho de construção — **não um
Protocol Python compartilhado.** `_build_executor` (`flow_runner.py`) permanece api-only
(sua mensagem de erro deixa de dizer "cli é 0015" e aponta o caminho certo); **o wiring do
`CliExecutor` mora no comportamento `dev-mini` do `FLOW_REGISTRY`** (comportamento é código
keyed por template, ADR-0016 §IV), nunca no runner genérico (runner fino, ADR-0016 §III).

### II. C1 — Disparo do flow dev é por CLI (terminal do dono), NUNCA pela UI; o gate continua na UI

Agente Claude Code roda **minutos**. Disparo síncrono no request da UI dispararia o
gatilho (b) do ADR-0018 (executor de minutos → o desenho síncrono morre, vira processo
executor com ADR próprio). Então o disparo é `kubo flow run dev-mini` no terminal do dono
(C1). O **gate continua na UI** (mecânica ADR-0018, intocada). A **assimetria com o
`analysis`** (que tem botão de disparo na UI) é **consciente e registrada aqui**: some
quando existir "processo executor" com ADR próprio.

`kubo flow run dev-mini` síncrono por minutos no terminal é o **mesmo trade-off do
ADR-0016 §VII** (crash = task órfã visível no board) **esticado de segundos para
minutos** — o gatilho (b) do ADR-0018 (processo executor) é a saída futura nomeada.

### III. C2 — Clone SEM credencial no workspace; PAT injetado só no push

Clonar com `https://x-access-token:PAT@github.com/...` grava o PAT em `.git/config` —
legível pelo agente com `cat`. Regra: o remote do workspace é **sempre URL sem
credencial**; o worker injeta o PAT **só no momento do push**, fora do alcance do agente
(credential helper efêmero ou header por comando), e **nunca loga a URL autenticada**
(mesma disciplina de redação do token do bot, ADR-0015). Com teste (§ Critérios do plano).

### IV. E1 — Env do agente por WHITELIST, nunca herança

O subprocess herda o env do pai por default — e o pai carrega SURREAL passwords,
`kubo_rw`, GEMINI, `TELEGRAM_BOT_TOKEN`, `GITHUB_PAT_FORGE`.

**Correção empírica (verificado no SDK pinado `==0.2.119`):** o SDK faz **MERGE** de
`os.environ` no subprocess (`subprocess_cli.py:491`: `inherited_env = os.environ` inteiro, e
`options.env` só **sobrepõe** por cima) — passar `options.env={...}` **NÃO basta**, o filho
ainda herda os segredos do pai. A whitelist REAL é **scrubbar `os.environ`** para só a
whitelist na janela do spawn, com restore integral no `finally`. Uma **fonte única**
(`_whitelist_env`) alimenta as duas camadas: o scrub (subtração dos segredos) e o
`options.env` (override de `HOME`→workspace, que sobrevive a drift de timing do SDK). `cwd`
pinado no workspace; `disallowed_tools` corta `WebFetch`/`WebSearch` (barateamento de
superfície a custo zero). O canário de env (teste que lê o env do subprocess) prova as DUAS
direções — segredos AUSENTES e `ANTHROPIC_API_KEY` PRESENTE — e **é parte do rito de bump do
SDK** (o timing do snapshot de env é interno não-documentado; bump revalida o canário).
Teto (§X): o scrub process-wide só vale no processo síncrono do CLI.

### V. E2 — Budget = teto-com-overshoot, dentro do `CliExecutor`, nunca no runner

O SDK reporta usage por mensagem e `total_cost_usd` no `ResultMessage`, mas o custo chega
**depois** do gasto do turno — o enforcement corta **logo após** estourar (overshoot ≤ 1
turno, nomeado). Backstops mecânicos no executor: `max_turns` + timeout de wall-clock.
Estouro → `ErrorInfo(kind="budget")` estruturado no `RunResult`. O runner permanece
camada fina (ADR-0016 §III — budget no runner = segundo mecanismo, parar na hora).

**No template: um escalar `budget_usd`** — enumera FATO, o runtime decide o que fazer
(lista negativa ADR-0016 §I intacta; congela no snapshot, invariante 4). O valor congelado
chega ao `CliExecutor` **pelo wiring do comportamento `dev-mini`**, nunca por `FlowCtx`/
`run_worker` (2º campo flow-específico no ctx = gatilho (b) do ADR-0016). O modelo
(`sonnet`) mora na **persona**, não no template.

NÃO entram: budget por estado, fallback de modelo, retry-on-budget. Isto **quita o gatilho
(c) do ADR-0016** (budget declarativo mínimo).

> **Condicional (Pergunta aberta 2):** se o SDK pinado NÃO expuser custo utilizável no
> stream, o budget **degrada para `max_turns`+timeout** e `budget_usd` **NÃO entra no
> template** (campo mentiroso = ADR-0016 §VIII). O campo só é adicionado ao loader
> (`extra="forbid"`) **depois** da evidência do spike.

**Camada EXTERNA do budget — spend limit do provider.** O teto interno (`budget_usd` +
`max_turns` + timeout no `CliExecutor`) protege o custo NORMAL; um **spend limit mensal
hard na conta da Anthropic** (console → Billing) é a camada que protege inclusive contra
**bug no enforcement interno** — se o check do executor falhar, o provider corta. Mesma
disciplina do spend limit do OpenRouter no dreno (ADR-0017). **Preparo do dono (16.7):** o
spend limit mensal hard é configurado **ANTES** de criar a `ANTHROPIC_API_KEY` — passo
literal no runbook §2d-style, valores nunca no chat (invariante 8). Duas camadas, uma
externa (provider) e uma interna (executor); a externa é o backstop de último recurso.

### VI. E3 — Estrutura NUNCA vem do texto do agente

Espelho de "citações nunca via LLM" (ADR-0016 §VI): URL do PR, nome do branch e todo dado
estrutural vêm das **respostas da API do GitHub**; o agente contribui **só prosa**. O
deliverable `kind=pr` guarda a URL que a API devolveu. Com teste.

**Emenda aditiva ao contrato (decisão do dono):** o `PrPayload` (url + number, tipados da
API — E3) ENTRA na união discriminada de payloads do ADR-0009 (`kubo/contracts/models.py`),
como **terceiro uso do idioma `DispatchPayload`/`ReportPayload`**: o payload espelha um
insert da store, e `runner._persist` ganha um case novo que mapeia `PrPayload` →
`insert_deliverable(kind="pr")`. O worker dev é um **Worker de contrato pleno** (manifest +
`run(ctx) -> RunResult`), e o PR volta como payload no `RunResult` — a persistência passa
pelo MESMO caminho do contrato. **Rejeitado:** o behavior persistir `kind="pr"` direto no
store (fora do `_persist`) — persistência fora do caminho do contrato é **segundo
mecanismo** (ADR-0016 §III). A emenda a `kubo/contracts/` é validada pelo advisor antes de
implementar. **Acoplamento 16.5↔16.6 aceito por desenho:** o critério do corte pós-16.5 é
**repo coerente e testado**, não rodável ponta a ponta — o end-to-end (`kubo flow run`,
gate, deliverable persistido) é a 0016b.

### VII. E4 — Relatório do agente no gate = untrusted no consumo

Saída de LLM que leu um filesystem inteiro. Renderizado como **texto plano, `pre-wrap`,
nunca markdown→HTML** — regime exato do `deliverable.content` (ADR-0018 §VI), sem exceção.

### VIII. E5/E7 — "Nada a pushar" = falha estruturada; identidade de commit; workspace efêmero

- Agente termina sem diff (ou só lixo) → task `failed`, **sem PR vazio** — caminho com
  teste. Branches órfãos (flow falhou pós-push): nome derivado do flow id (único por
  construção); limpeza = nota de runbook, não código.
- O worker configura `user.name`/`user.email` no workspace (**trava o run se faltar**).
- Workspace efêmero: `rm -rf` pós-run em `finally` (inclusive em falha), senão o disco do
  LXC enche de clones.

### IX. D38/E8 — Sem merge por capacidade; simetria anti-bypass no catálogo

`github.yaml` declara **SÓ** push / open-PR / close-PR-com-comentário — **merge ausente
por capacidade**, não por disciplina (D38; padrão ADR-0018 §V-bis). O `reason` do reject
(input do dono) vai pro comentário **sem interpolação esquisita**. Branch protection em
`main` do sandbox (C3) torna "worker só abre PR" verdade **por construção**.

### X. E6 — Limites honestos NOMEADOS, com gatilhos de migração

- **Agente roda no MESMO container/UID do Kubo** — `/proc/<pid>/environ` do pai entrega
  segredos a quem tiver RCE. Contenção real hoje = **conteúdo que o agente lê é do dono**
  (sandbox privado, task do dono, sem PRs de terceiros). **Gatilho: antes de a fase 4
  misturar conteúdo coletado/de terceiros no circuito do executor cli, o agente migra para
  container-irmão isolado.**
- **`permission_mode` headless** (bypass de permissões no subprocess): a contenção é
  workspace+env+conteúdo-do-dono, não prompts. O **invariante 5 NÃO está arranhado** — o
  gate humano está no PR, não no turno do agente.
- **Deps do projeto sandbox são código que executa** (`uv sync` roda postinstall de
  terceiros): sandbox minimalista, stdlib ou deps pinadas pelo dono.
- **Node.js na imagem** = runtime vendorizado de ferramenta terceira (como um binário),
  **não** linguagem de aplicação — **invariante 1 intacto**. Pin `claude-agent-sdk` +
  versão do CLI com evidência (disciplina ADR-0005).

### XI. Quitações de obrigações registradas em ADRs anteriores

- **ADR-0016, gatilho (c)** (budget declarativo mínimo): quitado por §V.
- **ADR-0009 §VIII regra 4** ("conteúdo coletado nunca flui para executor `cli` sem gate
  humano"): honrado **por construção nesta fase** — nenhum conteúdo coletado entra no
  circuito cli (task do dono + sandbox privado, escopo negativo do plano). O gatilho de
  container-irmão (§X) é a condição para isso mudar na fase 4.

## Perguntas abertas — RESOLVIDAS pelo spike 16.2

> O spike (16.2) rodou DENTRO da imagem do Kubo no kubo-test (LXC aninhado) e fechou as três
> por evidência: `turn_completed=True`, `num_turns=2`, `total_cost_usd=0.129413`, canário de
> env ABSENT. O status segue `proposto` até o cravar em 16.9 (validação final do advisor +
> reconciliação de custo do smoke), mas nenhum eixo abaixo é mais uma decisão em aberto.

1. **Onde vive o subprocess → IN-CONTAINER.** O LXC aninhado NÃO foi hostil ao spawn (turno
   trivial completou). Fica in-container; o gatilho de migração para container-irmão (§X)
   segue nomeado para a fase 4 (conteúdo de terceiros no circuito).
2. **SDK expõe custo utilizável → SIM.** `ResultMessage.total_cost_usd` veio populado
   (`0.129413`). `budget_usd` ENTRA no template (§V); o SDK ainda expõe `max_budget_usd`
   nativo, usado como camada extra além do check determinístico do executor.
3. **Node-na-imagem → NÃO precisa de Node separado.** O wheel `claude-agent-sdk==0.2.119`
   **vendoriza** um binário `claude` self-contained (241MB), arch-casado por uv (wheel
   `manylinux_2_17_aarch64`) — o CLI é vendorizado como binário (igual ao binário do
   tailwind), invariante 1 intacto. Custo nomeado e aceito: +240MB de imagem. O deploy
   buildou e o app sobe (smoke ok).

## Consequências

- **Positivo:** fatia vertical de fabricação de código de ponta a ponta; o esqueleto da
  fase 4 existe e foi provado com PR real; o gate humano (ADR-0018) ganha seu segundo
  consumidor sem mudança de mecânica.
- **Trade-off:** superfície nova (subprocess agêntico, credencial GitHub, Node na imagem);
  contida por env-whitelist (§IV), clone-sem-credencial (§III), sandbox privado + branch
  protection (§IX/§X) e os gatilhos de migração nomeados (§X).
- **Operação:** `ANTHROPIC_API_KEY` paga entra no circuito; custo real por flow reportado
  pelo SDK e reconciliado no smoke (16.9). Disco do LXC protegido pelo `rm -rf` em
  `finally` (§VIII).

## Alternativas rejeitadas

- **Protocol comum entre `api` e `cli`** — abstração prematura de uma implementação e, pior,
  derruba a fronteira de segurança do §I (worker de conteúdo coletado nunca pode receber
  executor agêntico). Rejeitada.
- **Disparo do flow dev pela UI** — dispararia o gatilho (b) do ADR-0018 (executor de
  minutos → processo executor). Adiado até esse ADR existir (§II).
- **Kubo faz o merge no aprovar** — bypass do gate por construção; viola o padrão
  anti-bypass (ADR-0018 §V-bis) e a fronteira do invariante 5. O merge é do dono (D38).
- **Clone com credencial embutida na URL** — grava o PAT em `.git/config`, legível pelo
  agente. Rejeitada (§III).
- **Herança de env pelo subprocess** — entrega SURREAL/TELEGRAM/PAT ao agente. Rejeitada
  por whitelist explícita (§IV).
- **Container-irmão isolado JÁ nesta sessão** — custo/complexidade sem necessidade enquanto
  o conteúdo lido é do dono; o gatilho de migração (§X) é a condição, não a data.
- **DevWorker fora do contrato ADR-0009 (orquestrador próprio com `DevOutcome`)** — seria o
  **segundo mecanismo** do ADR-0016 §III: reimplementa fora do `run_worker` o registro `run`
  (auditoria de execução de minutos que custa dólares), a fronteira exceção→`ErrorInfo` (E5
  cai de graça), a revalidação anti-TOCTOU do `RunResult` e o least-privilege R6 (PAT como
  `ResolvedIntegration` `repr=False` → C2 de graça). E erode o invariante 6 (contrato
  obrigatório) exatamente onde a fase 4 vai precisar — a máquina que PRODUZ workers da fase 4
  veste o contrato que os produtos vestirão (D35). Rejeitada.
- **`PrRef` por canal paralelo, `RunResult(payloads=[])` no sucesso (opção B)** — `RunResult`
  que mente + produto viajando fora do `payloads` + behavior escrevendo o deliverable direto
  = **dois escritores para um fato** (alternativa (b) já rejeitada no ADR-0016). Rejeitada.
