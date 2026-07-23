# ADR-0035 — Exposição da PRD: porta aberta + TLS próprio (Caddy)

> Status: **aceito** · Data: 2026-07-22 · Reverte a rejeição de "Caddy + port-forward" da sessão 0020 (`docs/sessions/0020-exposicao-kubo-oute-pro.md`), com motivo empírico nomeado.

## Contexto

A UI da PRD precisa ser acessível como **`https://kubo.oute.pro`** da internet. A sessão 0020 fixou (com o advisor) um desenho de **Cloudflare Tunnel** (`cloudflared` no compose, DNS proxied, zero porta aberta) e **rejeitou** a alternativa "Caddy + port-forward" com o motivo *"abre 443 no roteador doméstico, expõe IP residencial"*.

Dois fatos empíricos mudaram o cálculo ([KUBO-74](https://oute.atlassian.net/browse/KUBO-74), [KUBO-78](https://oute.atlassian.net/browse/KUBO-78)):

1. **O motivo da 0020 é nulo para a PRD.** A PRD roda no `oute-server`, uma **VPS na OCI com IP público real** (`140.238.238.118`), já exposto por um wildcard `*.oute.pro` — não há roteador doméstico nem IP residencial. A rejeição era específica do contexto DEV/casa.
2. **O túnel bateu num bloqueio.** O `cloudflared` (plano free) exige a **zona inteira na Cloudflare**, o que obriga migrar os nameservers da **Hostinger** (onde `oute.pro` vive hoje) para a CF. O dono tentou a migração e **bateu num bloqueio** (não diagnosticado — descartados DNSSEC e trava de domínio novo). Era exatamente o "replanejamento" que a 0020 antecipou como pré-condição.

Fato favorável levantado: do lado público, `80/443/2900` estão **fechados** no host hoje (nada serve web) — abrir a 443 é greenfield, sem proxy existente para conflitar. `kubo.oute.pro` já resolve para o host via wildcard.

## Decisão

### I. Caddy no compose termina o TLS na 443

`Caddy` como **container no compose** (portável, migra com `docker compose up` — mesmo papel que o `cloudflared` teria) termina o TLS na **443** e faz reverse-proxy para `kubo-api:8000` pela rede interna. Cert **Let's Encrypt via HTTP-01** → a regra da **security list da VCN abre 80 E 443** (o HTTP-01 precisa da 80). Registro **A explícito** `kubo.oute.pro → 140.238.238.118` (não confiar no wildcard). **DNS fica na Hostinger** — zero migração.

### II. Pré-condições inseparáveis (recicladas da 0020)

- **Cookie `Secure=True` GLOBAL** — nunca condicional por scheme. ⚠️ É flag global do código, e o **DEV (`kubo-test`) roda a mesma imagem** em `http://` — o primeiro deploy do código com `Secure=True` **quebra o login do DEV** sem `tailscale serve` (HTTPS no nome ts.net) subindo **também no kubo-test**, junto.
- **`X-Forwarded-Proto` confiado só do IP do Caddy** (`--forwarded-allow-ips`); sem isso o fluxo de login nasce `http://` e quebra.
- **`kubo.oute.pro` em `KUBO_ALLOWED_HOSTS`** (TrustedHostMiddleware, ADR-0014 §8).

### III. Risco de segurança nomeado

A exposição por porta-aberta é um **passo abaixo na superfície de rede** (porta aberta na internet, sem shield de DDoS de terceiro, IP de origem visível — mas já estava via wildcard). Isso **eleva a auth a portão único**: sem CF na frente, a autenticação (ADR-0036) + rate-limit se tornam o único portão. Em troca, um **ganho honesto**: o TLS termina na própria caixa, matando o MITM-na-borda que o túnel obrigava a aceitar. A regra da security list é **host-wide** (a VCN é compartilhada com os vizinhos do `oute-server`), não escopada ao Kubo.

## Consequências

- **Positivo:** zero migração de DNS; TLS ponta-a-ponta na caixa do dono; `Caddy` versionado no compose migra junto.
- **Trade-off:** porta aberta à internet eleva o peso da auth (ADR-0036 vira crítico); mudança host-wide na security list da VCN.
- **Registrado:** o bloqueio da migração de NS ficou **não-diagnosticado** — importa se a Cloudflare voltar à mesa (ex.: PRD em instância dedicada).

## Alternativas rejeitadas

- **Cloudflare Tunnel (desenho da 0020)** — exige a zona inteira na CF (free) e o dono bateu num bloqueio ao migrar os NS; reabrível só se a PRD sair do LXC para instância dedicada.
- **Tailscale Funnel** — só serve o domínio `ts.net`, morto pelo requisito do domínio próprio (0020).
