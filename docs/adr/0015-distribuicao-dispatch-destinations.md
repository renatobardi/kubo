# ADR-0015 — Distribuição: `dispatch` (fato) + `destinations.yaml` (para-quem)

> Status: **aceito** · Data: 2026-07-13

## Contexto

A fase 1 coleta (feeds), cura (destilação → grafo buscável, ADR-0013) e agora
precisa **entregar**: um digest diário dos destilados novos chegando ao dono
(Telegram, e-mail), com histórico auditável no grafo e telas de operação
(Envios/Destinos). A spec funcional prevê distribuição; falta decidir a **forma
de dados** de duas coisas — *para quem* se entrega e *o que* foi entregue — sem
violar os invariantes (um banco, três catálogos, sem workflow engine).

D11 (decisão do dono, sessão de planejamento): **Destino = pessoa** (dono ou
convidado) **OU sistema** (webhook/arquivo). Esta sessão implementa só o canal
pessoa (Telegram; e-mail se o timebox permitir); webhook/arquivo e convidados
ficam previstos, não construídos (sem consumidor ainda).

Este ADR **contraria e estende** dois ADRs anteriores e os nomeia:

- **ADR-0002** contém uma cláusula de contenção: "uma TERCEIRA tabela extra-spec
  é sinal de scope creep — para tudo e reabre planejamento." As duas tabelas
  extra-spec já existentes são `run` (ADR-0002) e `chunk` (ADR-0008, o vetor
  mora no chunk-como-registro). `dispatch` é a **terceira**. Esta sessão **é** a
  reabertura de planejamento que a cláusula exige; a análise abaixo é a prova de
  contenção.
- **ADR-0009** define o contrato de worker com uma união discriminada de
  payloads. O worker de digest precisa persistir um fato de entrega — exige um
  membro novo na união (`DispatchPayload`), emenda aditiva análoga à de
  `DistilledPayload` no ADR-0013.

## Decisão

### I. `destination` NÃO é tabela — é `destinations.yaml` na raiz (E1)

O eixo de decisão do ADR-0010 já fixou a prateleira: **catálogos = o quê**
(artefatos do ateliê), **`schedules.yaml` = quando** (operação). Destino é um
terceiro eixo — **para quem** — do mesmo tipo declarativo e operacional que
`schedules.yaml`. Logo mora ao lado dele, na raiz: **`destinations.yaml`**. Não
é um 4º catálogo (não descreve artefato); é o par "para-quem" do "quando".

Forma mínima, loader pydantic `extra="forbid"`:

```yaml
destinations:
  - id: owner-telegram
    name: Renato (Telegram)
    kind: pessoa            # pessoa | sistema
    channel: telegram       # telegram | email
    address_ref: env:KUBO_OWNER_TELEGRAM_CHAT_ID
  - id: owner-email          # forma-alvo; entra no arquivo DEPLOYADO só no 12.9,
    name: Renato (e-mail)    # junto com o sender de e-mail (ver §IV)
    kind: pessoa
    channel: email
    address_ref: env:KUBO_OWNER_EMAIL
```

**`KUBO_BASE_URL` (env) é resolvido pelo mesmo loader** que resolve
`address_ref` — o worker não lê `os.environ`; recebe destinos resolvidos + base
URL por injeção, reusando a máquina `env:VAR`. O loader vive em
`kubo/distribution/destinations.py` e o endereço resolvido fica em
`field(repr=False)` (PII: chat_id/e-mail nunca em repr/traceback — mesmo
fechamento por tipo do `ResolvedIntegration.secret`).

**`address_ref` é referência a env** (`env:VAR`), nunca valor inline: reusa
exatamente a máquina de `secret_ref` do catálogo de integrações (ADR-0009). Um
chat_id / e-mail é PII — fica fora do repo por construção, não por disciplina
(invariante 8). Isso resolve seed/cadastro de destino **sem migration, sem
tabela, sem UI de escrita** (EPIC-B): editar um destino é editar YAML + env.

### II. `dispatch` é a ÚNICA tabela nova — emenda ao ADR-0002 (E2)

`dispatch` é a **terceira tabela extra-spec** (depois de `run`, ADR-0002, e
`chunk`, ADR-0008), da mesma família de `run`: um **fato de execução**, não parte
do modelo de conhecimento.
Registra que um digest saiu (ou falhou) para um destino, num instante, cobrindo
até certa marca-d'água. Forma mínima:

```
dispatch:
  destination  string           # id do destino no YAML (não RecordID — o destino não é tabela)
  channel      string           # telegram | email
  status       string           # ok | error
  sent_at      datetime         # DEFAULT time::now()
  watermark    datetime         # max(distilled.created_at) do conjunto SELECIONADO
  item_count   int              # nº de destilados cobertos (`count` colide com count() do SurrealQL)
  error        option<object>   # FLEXIBLE — erro estruturado quando status=error
  items        option<array>    # RecordIDs dos distilled enviados (auditoria)
```

**SEM aresta `delivered`.** Duas razões, ambas do ADR-0008 §VI: (a) os endpoints
não preexistem no momento da entrega da forma que uma RELATION ENFORCED exige de
maneira barata; (b) **não há consumidor** — o mockup de Envios não tem
drill-down destino→destilado. `items` é um array de RecordIDs para auditoria
(quem foi no digest), não uma aresta do grafo. Aresta se cria quando (e se)
surgir um consumidor.

### III. Watermark — mecânica exata (não improvisar)

1. **watermark = `max(distilled.created_at)` do conjunto SELECIONADO.** Nunca
   `sent_at`: um distilled criado entre a query de seleção e o envio cairia num
   buraco eterno (nasceu antes de `sent_at`, mas não estava na query). `created_at`
   do próprio dado é a marca honesta. **"Conjunto selecionado" = tudo que a query
   trouxe, inclusive os sumarizados no "+N destilados — ver na UI"** quando a
   mensagem estoura o limite de 4096 (E4). Eles foram honestamente anunciados e o
   link da UI os cobre — logo o watermark os inclui, senão o "+N" viraria backlog
   permanente (trunca para sempre). São duas fronteiras distintas: o `limit` da
   query (o que sobra além dele tem `created_at > watermark` e flui para amanhã) e
   o truncamento de exibição "+N" (coberto pelo watermark).
   **Reconciliação de precisão (descoberta no smoke, decisão registrada):** o
   watermark faz round-trip pelo SDK (surrealdb 2.0.0), que trunca datetime a
   MICROSSEGUNDOS, enquanto `distilled.created_at` nasce de `time::now()` com
   precisão de NANOSSEGUNDOS. Comparar o created_at-ns cru contra o watermark-μs
   re-seleciona o último item enviado (a cauda de ns o faz `> watermark` — bola de
   neve). A seleção **pisa o `created_at` do banco a μs** (`time::floor(created_at,
   1us)` no WHERE); o watermark já chega em μs por construção (o `datetime` do
   Python/SDK não carrega ns, então nem o bind envia nem a leitura devolve cauda de
   ns). A fronteira do empate passa a ser μs — empate teórico em μs seria perdido,
   desprezível por construção (cada `insert_distilled` é transação própria).
2. **Por destination, e só dispatch `ok` avança.** Seleção do próximo digest =
   `distilled.created_at > watermark do último dispatch ok deste destination`.
   Consequência desejada: Telegram entregou mas e-mail falhou → o e-mail de
   amanhã reinclui os perdidos automaticamente, **sem lógica de retry**. O
   watermark é o mecanismo de retry, de graça.
3. **Bootstrap:** destino sem nenhum dispatch anterior → watermark = `now - 24h`.
   Senão o primeiro digest despejaria todos os destilados legados (935 do import
   Neon, ADR-0012) no Telegram do dono de uma vez. Uma janela de 24h no primeiro
   run é a fronteira; o dono vê "o de hoje", não "a história inteira".

### IV. Worker de digest sob contrato ADR-0009 — emenda aditiva (E3)

O digest é um worker sob o mesmo contrato (`run(ctx) -> RunResult`), não um
caminho paralelo. Três emendas aditivas, todas com precedente:

- **`DispatchPayload` entra na união `Payload`** (`type="dispatch"`), espelhando
  `insert_dispatch` da store, com um `case` novo no `_persist` do runner —
  exatamente como `DistilledPayload` fez no ADR-0013. **`DispatchPayload.items`
  é `list[str]`** (RecordIDs em forma string, validados por pattern estrito
  `^distilled:...$` na fronteira pydantic; a store converte para `RecordID` no
  `insert_dispatch`). **Exceção nomeada à disciplina de ref opaco** (ADR-0013): o
  digest worker é MECÂNICO, sem LLM no circuito — a razão do ref opaco (LLM
  forjando alvos de escrita num lote) não existe aqui. Os ids saem em forma
  string, leitura display-only (link da UI + auditoria); a disciplina de ref
  opaco permanece intacta para workers com LLM.
- **Um único método do seam `KnowledgeReader`:** `distilled_for_digest(destination,
  limit) -> list[DigestView]` — encapsula, na store, o watermark do último
  dispatch `ok` daquele destino + bootstrap `now-24h` + `created_at > watermark`.
  O worker fica burro: recebe a lista, monta o digest e computa `watermark =
  max(created_at)` das linhas devolvidas — nunca precisa conhecer o watermark
  anterior. `DigestView.created_at` é **`datetime`** (não `str`): alimenta o
  `max()` do watermark e o bind de volta ao banco (título via `derived_from`→item,
  entidades via `mentions`). Gatilho legítimo do ADR-0009 (método entra quando um
  worker exige leitura, com teste que justifique).
- **Telegram e SMTP viram integrações de catálogo** (spec §3.5): `telegram.yaml`
  (`secret_ref: env:TELEGRAM_BOT_TOKEN`), `smtp.yaml` idem. O manifest declara
  **só as integrações dos canais que o worker efetivamente serve** — `[telegram]`
  agora; `smtp` entra no manifest junto com o sender no 12.9. Fundamento: a
  resolução de integração é EAGER (`_build_context`) — declarar `smtp` com
  `SMTP_PASSWORD` ausente (e-mail é o 1º sacrifício!) mataria o run inteiro com
  `kind="config"` ANTES de qualquer envio, derrubando o digest do Telegram todo
  dia. O least-privilege existente resolve o acesso; o runner **não muda de
  arquitetura**.

**Entrega at-least-once (tensão nomeada e aceita).** Um crash entre o
`sendMessage` do Telegram e o `insert_dispatch` re-envia amanhã (watermark não
avançou). Pior caso: um digest duplicado para o **próprio dono** — aborrecimento,
não corrupção. Garantir exactly-once exigiria outbox / two-phase commit =
território de workflow engine (escopo negativo da spec §1.2). **Não construir.**

**Falha parcial** (Telegram ok, e-mail falhou): o worker devolve
`payloads=[dispatch(ok), dispatch(error)]` + `ErrorInfo(kind="dispatch_partial")`.
O ADR-0009 §VII já cobre payloads+error coexistindo — visível em Execuções (o
run) E em Envios (os dois dispatches).

### V. Só-se-novidade + corrida 09:00/09:30 (semântica aceita, não bug)

Dia sem destilado novo desde o último envio: o run fecha **`ok` com
`stats={new_distilled: 0}` e nenhum dispatch criado** — coerente com o ADR-0010
(run vazio é sucesso, não erro). Zero mensagem no Telegram.

O digest roda **09:30**, depois da destilação das **09:00** (ADR-0013). Se a
destilação das 09:00 ainda estiver rodando às 09:30, o digest daquele dia pega o
que já foi destilado e o resto entra no de amanhã (o watermark garante que nada
se perde). **Isto é semântica aceita, não bug:** os schedules são independentes
por design (ADR-0010), e o watermark torna a ordem de conclusão irrelevante para
a corretude — só afeta *quando* um destilado aparece, nunca *se* aparece.

## Consequências

- **Positivo:** entrega ponta a ponta sem segundo datastore, sem tabela de
  destino, sem UI de escrita, sem retry/outbox. O watermark-por-destino-só-ok é
  o retry de graça. Auditoria completa no grafo (`dispatch` + `items`). PII fora
  do repo por tipo.
- **Trade-off aceito:** at-least-once (digest duplicado no pior caso, só pro
  dono). Um único artefato de digest, um único conteúdo — sem digest configurável
  por destino nesta fase.
- **Contenção provada:** `dispatch` é a terceira e — pela análise E1 — a **última
  fácil**. `destination` deliberadamente NÃO-tabela é a prova de que a contenção
  do ADR-0002 está viva. Uma QUARTA tabela extra-spec reabre planejamento de novo.
- **Gatilhos registrados que reabrem esta modelagem:** (a) múltiplos artefatos de
  digest com queries por destino → destino talvez precise virar tabela; (b) split
  multi-mensagem se a quota do Groq subir e o digest passar a valer mais de uma
  mensagem; (c) qualquer consumidor de drill-down destino→destilado → aresta
  `delivered`. Nenhum existe hoje.

## Alternativas rejeitadas

(a) **`destination` como tabela** — rejeitada: sem consumidor de grafo para
destino, uma tabela + migration + UI de escrita (EPIC-B) é custo sem retorno;
YAML+env resolve seed e mantém PII fora do repo. (E1)

(b) **Aresta `delivered` distilled↔dispatch** — rejeitada: sem drill-down no
mockup, é aresta ENFORCED sem leitor; `items` (array de RecordIDs) cobre a
auditoria. (E2)

(c) **Outbox / two-phase commit para exactly-once** — rejeitada: é workflow
engine (escopo negativo §1.2); at-least-once com pior-caso "digest duplicado pro
dono" é aceitável. (E3)

(d) **Reusar o template Jinja da UI para montar o digest** — rejeitada: acoplaria
os canais (mudar a UI mexeria no Telegram); o builder de digest é puro e
separado. (12.3)

(e) **Watermark = `sent_at`** — rejeitada: distilled criado entre query e envio
cairia num buraco eterno; `max(created_at)` do conjunto enviado é a marca honesta.

## Nota — timezone de apresentação (fix)

Regra permanente do projeto: **todo datetime formatado para humano converte para a
tz local** (`env TZ`, default `America/Sao_Paulo`); **todo datetime armazenado ou
comparado permanece UTC**. Storage é UTC (SurrealDB `time::now()`); a conversão vive
só na borda de apresentação — `kubo/api/rendering.py` (`_local`), que cobre todas as
telas (incl. Envios/`sent_at`). Os builders de distribuição (digest/telegram) não
formatam datetime em texto humano hoje; se passarem a formatar, aplicam a mesma regra.
