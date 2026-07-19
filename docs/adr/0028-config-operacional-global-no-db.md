# ADR-0028 — Config operacional global no DB: horário do digest e pausa de distribuição editáveis pela UI

> Status: **aceito** · Data: 2026-07-19 · Validado pelo advisor (Fable 5) antes do crave.
> **Emenda o ADR-0010** (§I/§II — o "quando" mora no `schedules.yaml`) com uma exceção cirúrgica,
> e **emenda a frase-sinal do ADR-0025** (aposenta o tripwire da "oração subordinada" — ver Guardrails).
> Resolve o ticket wayfinder #119 (mapa #117); **2ª metade** da reabertura consciente iniciada
> pelo ADR-0027 (destino).

## Contexto

O ADR-0010 fixou que a agenda (o "quando") mora no `schedules.yaml` na raiz — "mudança de cron é
ajuste operacional, deploy sem code change". Serve bem para o encanamento: os sweeps (rss 08:00,
github-repo 07:00) e o distiller (09:00) são horários que o dono raramente toca e o gitops entrega.

Mas o **horário do digest** (09:30) é diferente: é o **único que o dono sente** — é quando o
Telegram/e-mail apita. O pedido do épico "destinos configuráveis" (mapa #117) inclui editar esse
horário e **pausar os envios (modo férias)** pela UI, sem deploy. Isso move o "quando" **do digest**
para o banco — uma emenda ao ADR-0010.

Este é o **par** do ADR-0027 (destino vira Cadastro): ambos nasceram do mesmo mapa #117, e o
ADR-0027 declarou nos seus Follow-ups nomeados que este ADR e ele "cravam juntos ou em sequência
consciente, prestação de contas única" das tabelas extra-spec. `settings` é a **5ª tabela
extra-spec** (run: ADR-0002; chunk: ADR-0008; dispatch: ADR-0015; destination: ADR-0027; settings:
aqui) — e é **qualitativamente diferente** das quatro anteriores: aquelas são *fatos* do domínio,
`settings` é *config operacional*. É a **primeira tabela de config**. Sequencial em vez de
simultâneo não afrouxa a cláusula de contenção do ADR-0002 — duas ADRs cross-referenciadas da mesma
sessão de mapa são mais conscientes que um ADR gordo. Este ADR **re-arma** a cláusula: uma 6ª tabela
extra-spec reabre planejamento.

## Decisão

1. **Tabela `settings` singleton** (migration 0012), **id fixo `settings:global`** — leitura por id,
   nunca `SELECT ... LIMIT 1` ambíguo; a unicidade vem do id fixo. Campos: `digest_cron` string +
   `distribution_paused` bool. **Singleton tipado (pydantic), não key-value genérico** — um KV
   stringly-typed é onde configs se acumulam sem review; o singleton tipado valida na borda e cada
   campo novo é código + PR (gate). A `0012` **pressupõe a `0011`/`destination`** (ADR-0027) cravada
   antes — a ordem das migrations segue a sequência consciente dos dois ADRs; se o build do 0028
   preceder o do 0027, reconciliar a numeração.

2. **O digest sai do `schedules.yaml`** e passa a ser dirigido por `settings`. `schedules.yaml` fica
   só com o encanamento de cron fixo (sweeps + distiller). `build_scheduler` **permanece puro** —
   recebe `Schedules` **e** `settings` por parâmetro; o `main` lê o DB (settings) e injeta. O job do
   digest é montado com **id estável `"digest"`**, trigger = `settings.digest_cron` **parseado com a
   tz do `schedules.yaml`**. O `max_items` (hoje `config: max_items: 50` na entry) vira **constante
   de código pinada** (espelho de `_DISTILLER_MODEL`). O driver é a **entry inteira sair do
   `schedules.yaml`** (não sobra casa senão o código), não uma superioridade do guardrail-em-código —
   tanto que o distiller **mantém** o `max_items` no YAML, sem contradição: cada `max_items` mora onde
   a sua entry mora.

3. **Dependência de DB no boot (mudança de contrato, registrada).** Hoje o scheduler sobe sem banco
   (jobs conectam no fire). Com o digest dirigido por `settings`, o boot **lê o DB**. `settings`
   ausente ou `digest_cron` inválido no boot → **`ConfigError`, o processo cai** (restart policy do
   compose; o deploy é o momento com um humano olhando). É a filosofia declarada do ADR-0010 ("falha
   alta antes do start, não horas depois") e evita a 6ª falha silenciosa da família D51 (um scheduler
   que sobe sem digest). O seed roda antes do `up` (deploy.sh), então settings existe no boot normal.

4. **Poll-and-reschedule (job de intervalo nativo, 5 min).** Não vem do YAML — é infraestrutura,
   adicionada por `build_scheduler`. **Assimetria eager/defensivo**, o mesmo padrão de
   `_add_sweep_job` (eager) × `execute_sweep_job` (defensivo):
   - Captura a **tz no boot** (closure) para montar o trigger novo — a tz vem do YAML, não da
     `settings`.
   - Relê `settings` numa conexão curta e compara `digest_cron` **como string** contra o **último
     cron aplicado guardado em memória**; só chama `scheduler.reschedule_job("digest", ...)` quando a
     **string diverge**. Comparar contra o `CronTrigger` atual não serve (APScheduler v3 não guarda o
     crontab original nem tem `__eq__` útil), e `reschedule_job` incondicional a cada tick poderia,
     no instante do disparo, recomputar `next_run_time` para amanhã e **perder o fire de hoje**
     (clobber). Com a comparação de string, o único clobber possível é no poll logo após uma edição
     do dono — e aí é exatamente o missed-fire já aceito (item 8).
   - **Defensivo em voo:** settings sumiu ou cron não parseia no poll → **loga erro, mantém o trigger
     atual**, tenta de novo no próximo tick; nunca derruba, nunca remove o job.
   - **Loga só em mudança ou erro** — nunca a cada tick (288 linhas/dia de "nada mudou" envenena o
     log). **Não cria `run`** (não polui a tabela Execuções).
   - Como o boot falha alto (item 3), o job `"digest"` **sempre existe** quando o poll roda —
     `JobLookupError` no `reschedule_job` é impossível por construção.

5. **`distribution_paused` checado no fire do digest** (sem reschedule). O job dispara, lê a flag; se
   `true`, **fecha `ok` com zero envio** — mesma semântica do só-se-novidade (ADR-0015 §V). Como não
   há `dispatch` escrito, **o watermark NÃO avança**: a pausa é **defer, não discard**. Pausar 30 dias
   e despausar entrega o acumulado, capado pelo `max_items` por digest, o resto flui nos dias
   seguintes (idêntico ao re-enable do ADR-0027 §9). **Critério de teste obrigatório:** "run pausada
   não escreve `dispatch` nem move watermark" — se alguém um dia fizer a pausa avançar o watermark, a
   pausa vira perda silenciosa de conteúdo. Efeito colateral aceito e nomeado: 30 dias de pausa = 30
   runs `ok` na tela Execuções.

6. **Nome honesto: `distribution_paused`, não `global_paused`.** A flag pausa **só a distribuição**
   (envio) — a coleta e a destilação continuam, o conhecimento acumula. Férias = "não me apite", não
   "congele o sistema" (decisão do dono: parar a coleta perderia a janela de RSS efêmero). "Global"
   faria parecer, em 6 meses, que a coleta parou — bug aparente. O "global" do ticket #119 era
   "config da instalação (global) vs config por-destino", não "pausa tudo". **Escopo pinado:** a flag
   é lida no **fire do digest agendado** (e no sweep de destinos, #121); o **report on-demand**
   (`kubo/api/routes/flows.py:47`, disparado pelo dono clicando) **ignora** a flag — é ação explícita
   do dono naquele instante, não entrega automática, e "não me apite" não deve barrar um relatório que
   ele mesmo acabou de pedir.

7. **Seed once-per-env** cria a `settings` (`digest_cron="30 9 * * *"`, `distribution_paused=false`),
   passo de deploy irmão das migrations (precedente #108/#118), **marker idempotente** — não
   ressuscita valores que o dono editou.

8. **Missed-fire aceito.** Mover 10:00→08:00 às 09:00 não dispara hoje (o reschedule computa o
   próximo fire para frente); o watermark garante que nada se perde, só desloca. Documentado, não
   implícito.

9. **`settings` NÃO carrega timezone.** Duas fontes de tz seria o bug; o trigger é **cron do DB + tz
   do YAML**, e o poll captura a tz no boot. Mudar a tz continua exigindo restart (já é verdade hoje)
   — a tz é do encanamento, não do ponteiro que o dono move.

10. **Validação de cron na borda de escrita.** A rota da UI (#122) valida o `digest_cron` com
    `CronTrigger.from_crontab` **antes** de gravar (APScheduler já é dependência do pacote — custo
    zero). Com isso + poll defensivo + boot eager, um cron inválido no banco só existe por edição
    manual do DB. **Staleness (409): dispensada explicitamente** — `settings` é singleton com um
    único escritor humano (o dono, pela UI) e o poll só lê; o molde de guarda 409 do #106 não se
    aplica (sem escritor concorrente). Uma edição perdida em 2 abas se resolve reeditando.

**Guardrails do invariante 7** — a linha nunca foi "onde mora o cron"; é (ADR-0025) valor×expressão,
dado×DAG, instância×tipo. Um cron string escolhido por humano numa UI continua "horário fixo
escolhido por humano"; mudou o meio (YAML→DB), não a natureza. Frase-sinal atualizada:

> *"O banco diz o quê coletar e para-quem entregar; o código diz como; o relógio diz quando — e o
> dono move o ponteiro do digest pela UI. **Mover ponteiro é dado; criar relógio é código.**"*

**Emenda à frase-sinal do ADR-0025.** O 0025 armou um tripwire — *"no dia em que essa frase precisar
de uma oração subordinada, passou da linha"* — e a frase acima **tem** uma. Esse tripwire era um
**proxy sintático** de um teste semântico; ele **cede** aos testes de fundo abaixo (valor×expressão,
dado×DAG, instância×tipo), que decidem a mesma pergunta com mais precisão. Um cron editável pela UI
passa nos três; a oração subordinada só nomeia *quem move o ponteiro*, não introduz lógica avaliada.
Este ADR **aposenta o proxy sintático** e mantém os testes semânticos como a linha canônica (o 0027
já havia relaxado o "fixo" da frase; este ADR fecha a conta explicitamente).

A linha que, cruzada, vira engine (gatilhos binários): agendamento **condicionado a evento/dado**
("dispara quando chegar item novo"); **linhas de tabela criando jobs** (uma tabela `schedules`
genérica onde inserir linha = job novo); **dependência entre jobs em dado** (teste do DAG do
ADR-0025). O conjunto de jobs permanece código + PR; o DB só move ponteiros de jobs que o código
declara.

## Consequências

- **Positivo:** o dono edita o horário do digest e pausa os envios pela UI, sem deploy — a dor do
  ADR-0010 ("cron é operacional") resolvida no ponto onde o operacional encosta no dono.
- **Trade-off aceito — híbrido YAML/DB do "quando".** Passam a existir duas casas para a agenda:
  sweeps/distiller no `schedules.yaml`, digest no DB. É a lição de dual-source do #108 (rejeitada no
  ADR-0027) — bancada aqui **só** porque o horário do digest é o único que o dono *sente*, e os
  sweeps são encanamento que gitops serve. **Gatilho de consolidação (com dentes):** no dia em que um
  **segundo** cron quiser ir pro DB, não se migra entry-por-entry (drift é como o híbrido apodrece) —
  **reabre-se a decisão e migra-se o "quando" inteiro**, aposentando o mecanismo YAML, num ADR
  próprio.
- **Trade-off — dependência de DB no boot** (item 3): DB fora do ar no boot = crash-loop visível (o
  comportamento desejado), não jobs falhando em silêncio horas depois.
- **Efeito nomeado — pausa longa = runs `ok` acumuladas** na tela Execuções (item 5).
- **Neutro:** a tela de config (horário + toggle de pausa) é nova superfície de escrita (#122), com
  helper honesto "aplica em até 5 min" (a latência do poll).

## Alternativas rejeitadas

- **Key-value genérico para settings:** stringly-typed, sem validação na borda, ímã de config sem
  review. Singleton tipado com pydantic é o certo.
- **Check-at-fire com cron fino** (avaliar o digest a cada 30 min e disparar na janela): parece mais
  simples, mas exige estado "já rodei hoje?" que o `dispatch` **não** fornece (dia sem novidade = run
  `ok` sem dispatch → ausência de dispatch não distingue "não rodou" de "rodou vazio"). Mais estado
  que o poll.
- **Live query do SurrealDB na `settings`** (push em vez de poll): cai pela mesma razão do ADR-0010
  §V — um ws de vida longa apodrece num processo que roda dias.
- **IPC/restart** (a API sinaliza ou reinicia o scheduler ao editar): é o 1º degrau do orquestrador;
  o poll existe exatamente para não haver canal entre os dois processos.
- **`global_paused` (pausar tudo, coleta inclusa):** perderia a janela de RSS efêmero; férias é "não
  me apite", não "congele o sistema".
- **Agenda por-destino** e **mover todas as schedules pro DB:** fora de escopo — só o horário do
  digest migra (a agenda é global, decisão do #119; a consolidação total é o gatilho acima).

## O que a emenda faz ao ADR-0010

- **§I/§II** (todo o "quando" no `schedules.yaml`) ganham a **exceção do digest** — o horário do
  digest migra para `settings`; o resto do YAML permanece.
- **§III** (registry hardcoded), **§IV** (BlockingScheduler + SIGTERM wait) e **§V** (conexão-por-job)
  permanecem intactos. O poll é um **job nativo novo**, não altera o ciclo de vida.

## Follow-ups nomeados (outros tickets do mapa #117)

- **#121** — o digest vira "sweep de destinos"; `distribution_paused` é checado **antes do loop** do
  sweep (curto-circuito único, não por-destino).
- **#122** — tela de config: editar `digest_cron` + toggle de pausa, com validação de cron na borda e
  o helper "aplica em até 5 min".
- **#123 / #124** — cutover do `destinations.yaml` e ops da porta de e-mail.
