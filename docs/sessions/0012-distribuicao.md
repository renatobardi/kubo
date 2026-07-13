# Sessão 0012 — Distribuição: o Kubo te entrega

> **Status:** aprovado pelo dono (2026-07-13, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas (stop-loss) — advisor estima 9–10,5h; sacrifícios PRÉ-DECLARADOS no fim da fila, disparam sem replanejamento
> **Estrutura:** 1 PR — branch `feat/0012-distribution` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

Digest diário dos destilados novos chegando no Telegram do dono (e e-mail), com histórico no grafo (tabela `dispatch`) e as telas Destinos/Envios na UI. Formaliza D11 no ADR-0015. **Critério físico: o digest real no Telegram do dono — provado no MEIO da sessão, não no fim.**

## Decisões do dono

- **D29:** canais = Telegram E e-mail (e-mail é o 1º sacrifício declarado se o timebox apertar).
- **D30:** digest diário **09:30** (após destilação 09:00), conteúdo = destilados novos desde o último envio (título + resumo curto + entidades + link pra UI). Dia sem novidade = **sem mensagem** (run fecha `ok` com `stats={new_distilled: 0}`, nenhum dispatch criado — coerente com ADR-0010).
- **D31:** telas Destinos + Envios nesta sessão (Destinos é o 2º sacrifício).
- **D11 formalizada (ADR-0015):** Destino = pessoa (dono/convidado) OU sistema (webhook/arquivo); nunca segundo datastore.

## Decisões fixadas pela consulta ao advisor (GO com E1–E7, todas incorporadas)

- **E1 — `destination` NÃO é tabela: é `destinations.yaml` na raiz** (precedente exato do ADR-0010: catálogos = *o quê*, schedules = *quando*, destinations = **para quem** — mesmo eixo, mesma prateleira, não é 4º catálogo). Loader pydantic `extra="forbid"`. Estrutura: `id, name, kind (pessoa|sistema), channel, address_ref` — endereços por referência a env (`address_ref: env:KUBO_OWNER_TELEGRAM_CHAT_ID` / `env:KUBO_OWNER_EMAIL`): reusa o formato `env:VAR` da máquina de `secret_ref`, PII fora do repo. Resolve seed/cadastro sem migration, sem EPIC-B.
- **E2 — `dispatch` é a ÚNICA tabela nova.** Forma mínima: `destination (string, id do YAML), channel, status (ok|error), sent_at, watermark (datetime), count, error (option, FLEXIBLE), items (option array<record<distilled>> — auditoria, NÃO aresta)`. **SEM aresta `delivered`** (endpoints não preexistem — ADR-0008 §VI; e não há consumidor — mockup de Envios não tem drill-down por destilado). **ADR-0015 emenda o ADR-0002 nomeadamente**: dispatch = 3ª tabela extra-spec, família de `run` (fato de execução); esta sessão É a reabertura de planejamento que a cláusula de contenção exige; destination deliberadamente não-tabela como prova de contenção.
- **Watermark (mecânica exata — não improvisar):**
  1. watermark = **`max(distilled.created_at)` do conjunto enviado** (nunca `sent_at` — distilled criado entre a query e o envio cairia num buraco eterno).
  2. **Por destination, e só dispatch `ok` avança**: seleção = `created_at > watermark do último dispatch ok deste destination`. Telegram ok + e-mail falhou → e-mail de amanhã inclui os perdidos automaticamente, sem lógica de retry.
  3. **Bootstrap:** sem dispatch anterior → `now - 24h` (senão o 1º digest despeja os 935 legados no Telegram). Registrado no ADR.
- **E3 — digest é worker sob contrato ADR-0009, com emenda aditiva:** `DispatchPayload` entra na união (`type="dispatch"`, espelha `insert_dispatch` da store, um `case` novo no `_persist`; como `DistilledPayload` no ADR-0013). Novo método do seam: `distilled_since(watermark, limit) -> list[DigestView]` (título via `derived_from`, entidades via `mentions`) — gatilho legítimo do ADR-0009. **Telegram e SMTP viram integrações de catálogo** (spec §3.5 literal): `telegram.yaml` com `secret_ref: env:TELEGRAM_BOT_TOKEN`, `smtp.yaml` idem; manifest declara `integrations: [telegram, smtp]`; least-privilege existente resolve. Runner NÃO muda de arquitetura. **Tensão nomeada e aceita: entrega at-least-once** — crash entre o sendMessage e o persist do dispatch re-envia amanhã; pior caso = digest duplicado pro próprio dono (aborrecimento, não corrupção). Outbox/two-phase = território de workflow engine (escopo negativo §1.2) — NÃO construir. Falha parcial: `payloads=[dispatch(ok), dispatch(error)]` + `ErrorInfo(kind="dispatch_partial")` — §VII já cobre, visível em Execuções E Envios.
- **E4 — Telegram: HTML parse mode** (MarkdownV2 rejeitado: 18 chars de escape sensíveis a contexto, footgun). Escaping = `html.escape` (stdlib), MESMA disciplina do XSS da 0009, com canários. Builder com whitelist: só `<b>` e `<a href>` do NOSSO template; todo conteúdo dinâmico (summary, título, **nomes de entidade — hostis também**, invariante de consumo do ADR-0013) escapado. **Único href = link pra UI via `KUBO_BASE_URL` (env nova) + record id — `item.url` coletada NUNCA vira hyperlink.** Limite 4096: **UMA mensagem, digest agrupado, truncamento honesto** ("+N destilados — ver na UI") **SÓ em fronteira de entry** (cortar dentro de `<b>` = HTML inválido = 400 = digest perdido = watermark não avança = bola de neve — teste obrigatório + smoke com digest artificialmente grande). Fallback barato a um parâmetro: texto puro sem parse_mode, se o Bot API rejeitar HTML em edge case.
- **Redação do token do bot (análogo do repr=False):** o Bot API põe o token na URL; exceções httpx embutem a URL e o truncamento de 500 chars do ADR-0009 NÃO salva. Sender captura e sanitiza antes de `ErrorInfo` — **com teste**.
- **E5 — E-mail: text/plain, stdlib** (`smtplib` + `email.message.EmailMessage`, zero dep — precedente scrypt). HTML rejeitado (importaria superfície XSS de clientes de e-mail pra entregar pro próprio dono). Superfície única: **header injection** — subject vem do template + data (nunca de coleta), mas o teste entra (asserta que EmailMessage rejeita newline em header). STARTTLS, creds env, provedor = dono define na hora (não definiu → sacrifício do e-mail dispara sozinho).
- **E6 — ordem de ataque: fatia Telegram deployada ANTES da UI** (critério físico na hora ~5).
- **E7 — timebox assume sacrifícios**: e-mail e Destinos no fim da fila.

## Marcos (ordem de ataque)

| # | Marco |
|---|---|
| 12.1 | **ADR-0015 esqueleto** (D11, E1/E2/E3, emenda ao ADR-0002, at-least-once, watermark, bootstrap, corrida 09:00/09:30 registrada como semântica aceita — não bug) |
| 12.2 | **Migration + store `dispatch`** (TDD, strict, validação linha a linha): `insert_dispatch`, `last_watermark(destination)`; loader do `destinations.yaml` |
| 12.3 | **Digest builder puro** (TDD): destilados → digest HTML-Telegram e texto-email; escaping com canários de injection (summary E nomes de entidade); truncamento em fronteira de entry testado. Builder separado do Jinja da UI — NÃO reusar template (acoplaria canais) |
| 12.4 | **Sender Telegram**: Bot API via httpx, integração `telegram.yaml` (secret_ref), redação de token testada |
| 12.5 | **Worker digest sob contrato**: DispatchPayload + seam `distilled_since` + manifest com integrations + entry 09:30 no schedules.yaml + só-se-novidade |
| 12.6 | **Deploy `./scripts/deploy.sh` + smoke físico (gated no "pode executar")**: digest real no Telegram do dono + digest artificialmente grande (truncamento) + re-run no-op (nada novo = nada enviado) |
| 12.7 | **UI Envios** (paridade EnviosScreen: artefato/canal/destino/quando/status; leitura de `dispatch`) |
| 12.8 | **UI Destinos** (paridade DistribuicaoScreen: loader do YAML + "Artefatos configurados" derivado do schedules.yaml. Desvios pré-declarados: "Novo artefato" fora de escopo — config é YAML; badge de convidado = dado inexistente) |
| 12.9 | **Sender e-mail** (E5) + destination e-mail no YAML |
| 12.10 | **ADR-0015 final (advisor valida antes de cravar)** + notas de execução |

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0015 antes de cravar.
2. **Extraordinária:** Bot API rejeitar o HTML gerado (fallback texto puro); qualquer tentação de outbox/retry/two-phase; múltiplos artefatos com queries por destino aparecerem como necessidade (reabre modelagem — gatilho registrado no ADR).
3. Conclusão da sessão.

## Tarefas do dono

- **Bot no BotFather** (a sessão te guia): criar bot, `TELEGRAM_BOT_TOKEN` no `.env` do servidor; **mandar uma mensagem pro bot ANTES** do getUpdates (senão vem vazio); `KUBO_OWNER_TELEGRAM_CHAT_ID` no `.env`.
- Provedor SMTP + credenciais (se quiser o e-mail nesta sessão; sem isso o sacrifício dispara sem drama). `KUBO_OWNER_EMAIL` no `.env`.
- `KUBO_BASE_URL` no `.env` (ex.: `http://100.66.254.24:3900`).
- **"Pode executar"** no deploy/smoke (12.6).

## Ordem de sacrifício

1. **1º:** e-mail inteiro (12.9 + smtp.yaml) — vira 0013, Telegram já entrega o valor.
2. **2º:** tela Destinos (12.8) — fica Envios.
3. **NUNCA cortáveis:** Telegram ponta a ponta provado fisicamente; dispatch + watermark com bootstrap; escaping com canários + truncamento em fronteira testado; redação de token; at-least-once documentado; ADR-0015 (com emenda ao ADR-0002).

## Critérios de aceite

- [ ] Digest real recebido no Telegram do dono (smoke físico), com título/resumo/entidades/link pra UI funcionando.
- [ ] Dia sem novidade: run `ok`, zero mensagem, zero dispatch (provado com re-run).
- [ ] Watermark por destination só avança em `ok`; bootstrap não despeja legado; falha parcial visível em Execuções e Envios.
- [ ] Canários de injection (markup Telegram via summary/entidade) passam; truncamento em fronteira de entry testado; token jamais em erro/log (teste).
- [ ] `destinations.yaml` + integrações telegram/smtp no catálogo com secret/address por referência (invariante 8).
- [ ] UI Envios (e Destinos, se não sacrificada) com tabela de paridade conferida.
- [ ] Cobertura ≥85%; ADR-0015 mergeado emendando ADR-0002 e ADR-0009; PR conforme; main verificado.
- [ ] Notas: fila pra 0013 (e-mail se cortado), gatilhos registrados (multi-artefato, split multi-mensagem se quota do Groq subir).

## Escopo negativo da sessão

- Tabela `destination` NÃO (YAML — E1). Aresta `delivered` NÃO (sem consumidor). Outbox/retry/two-phase NÃO (§1.2).
- MarkdownV2 NÃO. E-mail HTML NÃO. `item.url` coletada como hyperlink NÃO. Split multi-mensagem NÃO (gatilho registrado).
- Webhook/arquivo como destino NÃO (D11 os prevê; implementação quando houver consumidor). Convidados NÃO.
- Escrita de destination pela UI NÃO (EPIC-B). Digest configurável por destino NÃO (um artefato, um conteúdo).
- Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-13; decisões do dono D29–D31 + D11; consulta de validação ao advisor (Fable 5): GO com emendas E1–E7, todas incorporadas — destination como YAML declarativo (eixo o-quê/quando/para-quem), dispatch única tabela nova com watermark max(created_at)-por-destination-só-ok + bootstrap now-24h, worker sob contrato com DispatchPayload/seam/integrações de catálogo e at-least-once nomeado, Telegram HTML com escaping stdlib + truncamento em fronteira + redação de token, e-mail text/plain stdlib com teste de header injection, fatia Telegram deployada antes da UI, timebox com sacrifícios pré-declarados.*

---

## Notas de execução (2026-07-13, CLI Opus + advisor Fable 5)

**Entregue:** 12.1–12.8 + 12.10. **Sacrificado:** 12.9 (e-mail, 1º sacrifício
pré-declarado — o dono não forneceu SMTP; Telegram já entrega o valor).
Destinos (12.8, 2º sacrifício) **NÃO** foi cortada — entregue acima da fila.

Gates: ruff + ruff format + pyright (0 erros) + 449 testes verdes + cobertura
98,43% em store/contracts/runtime. Smoke físico provado no meio da sessão.

### Registros (drift plano→código e emendas de critério)

1. **Seam renomeado `distilled_since` → `distilled_for_digest(destination, limit)`**
   — mudança para melhor: encapsula watermark + bootstrap na store; o worker nunca
   conhece o watermark anterior (computa `max(created_at)` do conjunto devolvido). O
   ADR-0015 §IV registra a forma final.
2. **`smtp.yaml` + teste de header injection foram junto no sacrifício do e-mail** —
   o critério de aceite "integrações telegram/**smtp** no catálogo" fica emendado
   para só-telegram nesta sessão; smtp/header-injection entram na 0013 com o sender.
3. **Bug de precisão datetime encontrado E corrigido no smoke:** o watermark faz
   round-trip pelo SDK em μs, mas `distilled.created_at` nasce ns (`time::now()`); a
   seleção re-enviava o último item (cauda de ns > watermark μs — bola de neve). Fix:
   `time::floor(created_at, 1us)` no WHERE. Guardado por `test_watermark_round_trip`
   (semeando com `time::now()` do servidor) + `test_digest_vertical` (re-run no-op).

### Smoke físico (kubo-test, provado)

- ✅ Digest real no Telegram do dono (2 destilados; link "abrir no Kubo" → detalhe OK).
- ✅ Re-run sem novidade: `ok`, zero mensagem, zero dispatch novo.
- ✅ Digest grande truncado (3921 chars, rodapé "+N", HTML balanceado) aceito pelo Bot API — sem 400.
- Deploy: `COPY destinations.yaml` no Dockerfile + 3 envs no compose do scheduler (TELEGRAM_BOT_TOKEN, KUBO_OWNER_TELEGRAM_CHAT_ID, KUBO_BASE_URL).

### Fila para 0013

- **E-mail** inteiro (12.9): sender `smtplib`+`EmailMessage` text/plain, teste de
  header injection, `smtp.yaml` (secret_ref), entry `owner-email` no destinations.yaml.
- Gatilhos de reabertura de modelagem (ADR-0015): split multi-mensagem se a quota do
  Groq subir; destino vira tabela se surgir digest por-destino/multi-artefato; aresta
  `delivered` se surgir consumidor de drill-down.

### Paridade de UI — tabela (aceite: screenshot lado a lado, ver abaixo)

**Envios** (mockup `EnviosScreen` em `DistribuicaoScreen.jsx`):

| Elemento | Status |
|---|---|
| PageHeader "Envios" + descrição | igual |
| SearchBar (artefato/canal/destino) | igual (busca canal/destino/status na store) |
| Linha: ícone + artefato (`kind`) | igual (glifo `send` + "Digest") |
| Badge de canal | igual |
| Destino + quando | igual |
| Status ok/erro + erro expansível | adição do plano (D declarado) — presente |
| ViewToggle list/grid2 | desvio: só lista (grid2 = luxo cortável, mesma decisão dos Destilados) |
| Estado vazio | igual |
| Paginação | igual (peek/total, como Execuções) |

**Destinos** (mockup `DestinosScreen`):

| Elemento | Status |
|---|---|
| PageHeader "Destinos" + descrição | igual |
| Card "Artefatos configurados" (nome/agenda/origem/destinos) | igual (do schedules.yaml) |
| Card "Destinos" (avatar+nome+kind+canal) | igual (do destinations.yaml) |
| Ação "Novo artefato" | fora de escopo (config é YAML+PR — desvio pré-declarado no plano) |
| Badge de convidado | fora de escopo (dado inexistente — sem convidados nesta fase, desvio pré-declarado) |
| Badge de role "dono" | igual |

Screenshots lado a lado das duas telas: anexados pelo dono (a UI é autenticada; o
agente não tem a senha) — pendentes no PR antes do merge.
