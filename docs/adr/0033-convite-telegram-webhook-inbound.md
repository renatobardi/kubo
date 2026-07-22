# ADR-0033 — Convite de onboarding Telegram: webhook inbound + tabela `invite` extra-spec

> Status: **aceito** · Data: 2026-07-21 · Validado pelo advisor (Fable 5) antes do crave.

## Contexto

A feature de onboarding de destinos Telegram por convite está mapeada em **KUBO-58** (mapa
wayfinder) com spec em **KUBO-66** (já publicado). O dono quer: criar o destino sem endereço,
o Kubo gera um convite (token + link), o dono entrega esse link ao amigo (e-mail automático ou
link copiável). O amigo clica — isso abre o Telegram e dispara `/start <token>` pro bot. O lado
do convidado é inteiramente dentro do Telegram; ele nunca toca a UI do Kubo. O Telegram entrega
esse `/start` ao Kubo via **webhook**; o Kubo valida o token, captura o `chat_id` e cria o
`destination` automaticamente — sem confirmação manual do dono. Fluxo não existe hoje —
`destination` nasce só pelo seed (`owner-telegram`, o único).

Essa feature comporta **duas decisões técnicas de arquitetura já finalizadas** (KUBO-59 a
KUBO-64, tickets de pesquisa e decisão, todos fechados):

1. **Webhook vs polling** — qual o mecanismo de o Kubo saber que o amigo aceitou o convite
   no Bot API. Webhook (Telegram chama Kubo) é a topologia alvo, alinhada ao PRD (Kubo exposto
   na OCI/VCN do dono, não escondido). Polling (`getUpdates`) era a alternativa menos-infra
   (funciona sem HTTPS/portas, mesmo em DEV), mas o dono quer a arquitetura já PRD-shaped.
2. **Modelagem de convite** — se vira um campo extra em `destination`, ou uma tabela separada.
   Separado resolve de graça (zero mudança no schema existente `destination`).

Gatilho 1: **tabela extra-spec inédita na onda atual.** O ADR-0002 §III decreta que toda
tabela fora do schema original da spec funcional é uma reabertura consciente de planejamento.
Conta: `run` (ADR-0002), `chunk` (ADR-0008), `dispatch` (ADR-0015), `destination`
(ADR-0027), `settings` (ADR-0028, aceito em 2026-07-19). `invite` seria a **6ª** tabela
extra-spec. A cláusula do ADR-0002 torna cada uma dessas uma reabertura consciente — esta ADR
é esse ato pra `invite`.

Gatilho 2: **inbound de rede inédito.** Até agora Kubo só ENVIA (Telegram `sendMessage`, SMTP).
Webhook é o primeiro endpoint que RECEBE chamadas externas não-autenticadas por sessão (bem,
autenticadas por secret header, não sessão Bearer). Não é coberto por nenhuma ADR existente
(ADR-0014 regula o guard de login; este webhook CONTORNA conscientemente).

Contexto de infraestrutura: a decisão de webhook assume Kubo **exposto e alcançável por
HTTPS público.** `setWebhook` só aceita HTTPS numa das portas **443, 80, 88 ou 8443** (fato
verificado por fetch direto na doc oficial `core.telegram.org/bots/api#setwebhook`) — em DEV
(Tailscale-only, ADR-0011), nada escuta nessas portas publicamente, então o Telegram nunca
chamará o webhook real. O Kubo testa a rota só via FastAPI TestClient com payloads Update
sintéticos. A chamada real de `setWebhook` (que registra no Bot API) fica pra uma frente de
infra PRD separada (fora deste escopo).

## Decisão

1. **Webhook, não polling.** O mecanismo de o Kubo saber de convites aceitos é o Telegram
   chamando a rota `POST /telegram/webhook` com updates (payload `Update` JSON). **Decisão nova
   desta ADR:** a rota exata é `POST /telegram/webhook`. Trade-off: a prova física do webhook
   é impossível em DEV; o teste é unitário (TestClient + payload sintético).

2. **Tabela `invite` nova, separada de `destination`.**
   - Não estende `destination` com campos opcionais — a tabela `destination` tem `address`
     (string obrigatório) com índice `UNIQUE(channel, address)` (migration 0011). Pendente de
     aceite não tem `address` ainda. Tornar `address` opcional requer rethink da unicidade;
     tabela separada evita isso (custo zero, schema intocado).
   - **Lifecycle:** convite pendente → aceito. O aceite CRIA um `destination` novo (item 6) —
     o convite não é transformado nem apagado, continua existindo com `accepted_at` setado.
     Não há estado de "convite rejeitado" — token expirado é o único encerramento negativo.

3. **Campos de `invite`:**
   - `name` string (nome do destino-a-ser)
   - `email` option\<string\> (endereço do convidado; PII, mesma disciplina do
     `destination.address`)
   - `token` string (id do convite; `secrets.token_hex(16)`, 32 chars) — **index UNIQUE**
   - `expires_at` datetime (TTL 7 dias)
   - `accepted_at` option\<datetime\>
   - `created_at` datetime
   - **Status é calculado**, nunca armazenado: `pending` (criado, `accepted_at` IS NONE,
     `expires_at` > now), `expired` (created, `accepted_at` IS NONE, `expires_at` ≤ now),
     `accepted` (`accepted_at` IS NOT NONE). Nenhum `enum_status` na tabela.

4. **Rota do webhook é pública e fora do guard de login.** Novo módulo `kubo/api/routes/telegram_webhook.py`,
   endpoint registrado em `_PUBLIC_PATHS`/`_PUBLIC_PREFIXES` em `kubo/api/app.py` (mesmo nível
   de `/login`, `/healthz`, `/static/`). Regida por ADR-0014, que agora cobre webhook como
   caminho de inbound.

5. **Autenticação do webhook: header `X-Telegram-Bot-Api-Secret-Token` obrigatório.** O
   `secret_token` é opcional na API do Telegram, mas Kubo o torna mandatório na sua
   implementação — postura "entrada externa é hostil" (regra de segurança, CLAUDE.md §9).
   Header ausente/incorreto → resposta 401 imediatamente, antes de parsear corpo. Motivo:
   qualquer terceiro com conhecimento da URL pode spammar payloads `Update` malformados.

6. **Aceitação de convite gera `destination` — com falha segura em colisão.** Quando um convite
   é aceito (token validado, chat_id resolvido no Telegram), a rota do webhook chama
   `store.accept_invite(invite_id, chat_id)` que: atomicamente (single transaction):
   - **checa `UNIQUE(channel, address)` ANTES de setar `accepted_at`** — se colisão (chat_id
     já existe em outro destino), retorna erro (o convite fica pendente, expira normalmente, o
     dono pode reenviar) e **loga WARNING** (única severidade alta do fluxo)
   - se sem colisão: seta `invite.accepted_at = now` **e** cria um `destination` novo com
     `channel='telegram'`, `address=<chat_id normalizado>`, `name=invite.name`

7. **Secret management:** o `secret_token` é único por ambiente (DEV ≠ PRD). **Decisão nova
   desta ADR:** lido de env `KUBO_TELEGRAM_WEBHOOK_SECRET` — obrigatório (falta = erro de
   config). Nunca literal em código/seed. Segue a mesma disciplina de bot token (que já é
   env-only, ADR-0031).

8. **Response do webhook: sempre 200, mesmo em erro de conteúdo.** Um convite com token
   inválido/expirado/reusado, payload malformado, ou colisão de chat_id, retorna HTTP 200
   (sucesso comunicativo) com JSON `{"status": "ok"}` vazio — **nunca tira-do-retorno do
   Telegram, nunca retry-storm do lado do Telegram.** A honestidade: o erro é logado
   estruturado com contexto (invite_id, razão, chat_id oferecido) mas não surfaced como
   HTTP error — é dado, não exceção da rede.

9. **`setWebhook` real só em PRD.** Kubo não chama `POST https://api.telegram.org/bot.../setWebhook`
   automaticamente. Um script/playbook de deploy PRD fará isso (fora deste escopo); DEV nunca
   o chama (Tailscale não satisfaz HTTPS). Mudança de webhook (URL, secret, authorized_only)
   fica manual/operacional, não code-deployed.

## Consequências

- **Positivo:** webhook é a topologia alvo de produção, sem atalhos. O modelo de convite
  (tabela separada, transição explícita em `destination`) é limpo e reusável (histórico de
  convites fica auditável). 6ª tabela extra-spec é registrada conscientemente, não por acidente.
- **Trade-off:** DEV não permite testar o webhook de verdade — só TestClient. A validação de
  produção do mecanismo real (rede, HTTPS, secret, Bot API) ocorre só em PRD. Até lá, é
  cobertura só do lado de código, não ponta-a-ponta física.
- **Neutro:** nova superfície inbound (POST /telegram/webhook) abre a porta de segurança de
  inbound para o projeto — a defesa é o header secret + parsing restritivo de `Update` +
  resposta sempre-200 (sem gadget de retry amplificado).

## Alternativas rejeitadas

- **Polling (`getUpdates`):** menos infraestrutura (funciona em DEV+PRD sem HTTPS/exposição),
  mas não alinha ao PRD (Kubo exposto). Rejeitada por decisão explícita do dono.
- **Convite como campos extras em `destination`:** torna `address` opcional + rethink de
  `UNIQUE(channel, address)` — custo desnecessário, tabela separada resolve de graça.
- **Tabela única `invite_destination` ou renomear:** separação clara (invite → destination
  é transição, não bifurcação) é melhor que nome único enganoso ou composição forçada.
- **Autenticação por Bearer token / OAuth:** webhook do Telegram chega como HTTP POST simples,
  sem Header Authorization tradicional. Header customizado (`X-Telegram-Bot-Api-Secret-Token`)
  é o padrão dele; Bearer seria falsa padronização.
- **Armazenar `secret_token` no banco (cifrado):** segredo nunca vai pro banco — env-only
  (invariante 8). Mesma postura do bot token (ADR-0031).
- **Estender ADR-0027 ou ADR-0031:** 0027 trata schema de destinos (tabela, campos, lifecycle);
  0031 trata sender SMTP (integração, credencial env). Inbound é preocupação distinta
  (segurança, rede, autenticação) que merece registro próprio.
