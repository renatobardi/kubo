# ADR-0038 — Identidade de distribuição da PRD: e-mail (Resend) + canais Telegram

> Status: **aceito** · Data: 2026-07-22 · **Estende o ADR-0031** (credenciais SMTP por env) e **aplica os ADR-0027/0029/0030** (destinos no DB) ao ambiente de produção.

## Contexto

O mapa de wayfinder [KUBO-72](https://oute.atlassian.net/browse/KUBO-72) leva o Kubo a produção. O dono decidiu (D-e) que `oute.pro` hoje é **só domínio**, sem provedor de e-mail. A PRD precisa de uma **identidade de e-mail própria** (`@kubo.oute.pro`) e o **bot Telegram + chat reais** — que hoje servem o digest diário no `kubo-test` (sessão 0012, vivo) — precisam de **direção na migração** para a PRD, já que o DEV vira homologação com canais de **teste** próprios (D-c). Decisões em [KUBO-81](https://oute.atlassian.net/browse/KUBO-81) e [KUBO-82](https://oute.atlassian.net/browse/KUBO-82).

## Decisão

### I. E-mail: só-envia via Resend (plano free)

A PRD envia de **`kubo@kubo.oute.pro`** (subdomínio — protege a reputação de `oute.pro`; um digest automatizado marcado como spam não arrasta o domínio raiz). Provedor **Resend, plano free** (3.000/mês contra dezenas necessárias, SMTP nativo, sem marca no corpo). **Só envia** — sem caixa; um header **`Reply-To` → e-mail pessoal do dono** cobre resposta humana. Bounces aparecem no painel do Resend.

Fatos que dissolveram as preocupações (KUBO-76): a **porta 25 da OCI é irrelevante** (submissão autenticada é 587/465) e **todos os candidatos oferecem relay SMTP** — logo **zero reescrita do `EmailDigestWorker`** (ADR-0031), em qualquer cenário.

**DNS** (na zona Hostinger — a PRD não migrou para a Cloudflare, ADR-0035): SPF e DKIM saem do painel do Resend ao verificar `kubo.oute.pro`; **DMARC** em `_dmarc.kubo.oute.pro` começando em **`p=none`** com `rua` para o e-mail pessoal (observar, ler relatórios, só então endurecer). **Env** (ADR-0031): `KUBO_EMAIL_HOST=smtp.resend.com`, porta 465/587, `KUBO_EMAIL_USER=resend`, `KUBO_EMAIL_PASSWORD` = a API key do Resend (**segredo** — só no `.env` da PRD), `KUBO_EMAIL_FROM=kubo@kubo.oute.pro`.

**Build:** adicionar o header `Reply-To` na construção do e-mail (o `SmtpConfig` do ADR-0031 só tem `from`) — a caixa `@kubo.oute.pro` não recebe.

### II. Canais Telegram: o bot real migra para a PRD

O **bot Telegram real reusa o token atual na PRD** (mantém o histórico e o chat que o dono já usa; continuidade zero-atrito). O **DEV ganha um bot de teste NOVO** (token próprio + chat de teste). Os **destinos reais** (chat do digest, e-mail) são **recadastrados na PRD** (via UI/seed) — o D-d migrou só as **fontes**; a PRD nasce sem destinos (destinos vivem no DB, ADR-0027/0030).

### III. Fail-safe do DEV: estrutural, sem flag de runtime

O DEV é impedido de escrever nos canais reais por **separação de credencial e de dado**, não por convenção:

1. **Token:** o Telegram aceita **um consumidor por token** (um webhook / um polling). O token real fica preso ao consumidor da PRD; o DEV tem token de teste próprio → o DEV **fisicamente não consegue** enviar como o bot real.
2. **Destino:** o chat real e o e-mail real vivem **só no banco da PRD** (D-d não migrou destinos); o banco do DEV só tem destinos de teste → o DEV **não conhece** os canais reais.

Ambas caem das decisões já tomadas: credenciais separadas por ambiente (ADR-0034 §IV) + PRD com banco vazio (D-d). O que sustenta = o rito do `.env`/banco por-ambiente (invariante 8): o token real e o chat real só existem na PRD.

**Cutover:** o `kubo-test` **solta o token real primeiro** (para o scheduler / limpa o consumidor) → a PRD assume o token → o `kubo-test` recebe o token de teste novo. Sem essa ordem, os dois brigam pelo token durante a virada.

## Consequências

- **Positivo:** zero reescrita do worker de e-mail; o fail-safe do DEV é estrutural (não há flag a esquecer); continuidade do bot real com histórico.
- **Trade-off:** e-mail "só-envia" significa que ninguém responde a `@kubo.oute.pro` — mitigado pelo `Reply-To` pessoal; reabre se `oute.pro` virar identidade de e-mail além do digest.
- **Workstream de build:** o seed só-fontes DEV→PRD, o fail-safe dos canais e esta direção são **uma frente só** — o mesmo `.env`/banco por-ambiente resolve os três.

## Alternativas rejeitadas

- **Caixa de e-mail completa** (Purelymail/Migadu, ~US$10–19/ano) — adiciona MX + senha + serviço a manter, sem valor para o caso de uso (digest, saída pura).
- **Envio do domínio raiz `@oute.pro`** — arrasta a reputação do raiz; o subdomínio isola.
- **Bot novo para a PRD** (o real vira o de teste do DEV) — mais disrupção (iniciar chat novo) sem ganho; o dono preferiu reusar o token.
- **Flag de runtime "não enviar em DEV"** — frágil (esquecível) contra a separação estrutural de credencial e dado.
