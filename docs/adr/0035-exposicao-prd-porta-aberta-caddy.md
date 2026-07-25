# ADR-0035 — Exposição da PRD: porta aberta + TLS próprio (Caddy)

> Status: **aceito** · Data: 2026-07-22 · Reverte a rejeição de "Caddy + port-forward" da sessão 0020 (`docs/sessions/0020-exposicao-kubo-oute-pro.md`), com motivo empírico nomeado.
> **⚠️ Mecanismo de TLS SUPERADO em 2026-07-25:** o §Decisão I (Caddy no compose) é **histórico** — a exposição **vigente** é **nginx do host + certbot**, não Caddy; a premissa central era falsa. A decisão de fundo (porta-aberta + TLS na própria caixa) permanece. Ver §Atualização ao final.

## Contexto

A UI da PRD precisa ser acessível como **`https://kubo.oute.pro`** da internet. A sessão 0020 fixou (com o advisor) um desenho de **Cloudflare Tunnel** (`cloudflared` no compose, DNS proxied, zero porta aberta) e **rejeitou** a alternativa "Caddy + port-forward" com o motivo *"abre 443 no roteador doméstico, expõe IP residencial"*.

Dois fatos empíricos mudaram o cálculo ([KUBO-74](https://oute.atlassian.net/browse/KUBO-74), [KUBO-78](https://oute.atlassian.net/browse/KUBO-78)):

1. **O motivo da 0020 é nulo para a PRD.** A PRD roda no `oute-server`, uma **VPS na OCI com IP público real** (`140.238.238.118`), já exposto por um wildcard `*.oute.pro` — não há roteador doméstico nem IP residencial. A rejeição era específica do contexto DEV/casa.
2. **O túnel bateu num bloqueio.** O `cloudflared` (plano free) exige a **zona inteira na Cloudflare**, o que obriga migrar os nameservers da **Hostinger** (onde `oute.pro` vive hoje) para a CF. O dono tentou a migração e **bateu num bloqueio** (não diagnosticado — descartados DNSSEC e trava de domínio novo). Era exatamente o "replanejamento" que a 0020 antecipou como pré-condição.

Fato favorável levantado: do lado público, `80/443/2900` estão **fechados** no host hoje (nada serve web) — abrir a 443 é greenfield, sem proxy existente para conflitar. `kubo.oute.pro` já resolve para o host via wildcard.

## Decisão

### I. Caddy no compose termina o TLS na 443

> **[SUPERADO 2026-07-25 — histórico]** A decisão **vigente** é **nginx do host + certbot** (ver §Atualização ao final). O texto abaixo é o registro do que se decidiu em 2026-07-22, mantido como histórico — não é o que está no ar.

`Caddy` como **container no compose** (portável, migra com `docker compose up` — mesmo papel que o `cloudflared` teria) termina o TLS na **443** e faz reverse-proxy para `kubo-api:8000` pela rede interna. Cert **Let's Encrypt via HTTP-01** → a regra da **security list da VCN abre 80 E 443** (o HTTP-01 precisa da 80). Registro **A explícito** `kubo.oute.pro → 140.238.238.118` (não confiar no wildcard). **DNS fica na Hostinger** — zero migração.

### II. Pré-condições inseparáveis (recicladas da 0020)

- **Cookie `Secure=True` GLOBAL** — nunca condicional por scheme. ⚠️ É flag global do código, e o **DEV (`kubo-test`) roda a mesma imagem** em `http://` — o primeiro deploy do código com `Secure=True` **quebra o login do DEV** sem `tailscale serve` (HTTPS no nome ts.net) subindo **também no kubo-test**, junto.
- **`X-Forwarded-Proto` confiado só do IP do Caddy** (`--forwarded-allow-ips`); sem isso o fluxo de login nasce `http://` e quebra.
- **`kubo.oute.pro` em `KUBO_ALLOWED_HOSTS`** (TrustedHostMiddleware, ADR-0014 §8).

### III. Risco de segurança nomeado

A exposição por porta-aberta é um **passo abaixo na superfície de rede** (porta aberta na internet, sem shield de DDoS de terceiro, IP de origem visível — mas já estava via wildcard). Isso **eleva a auth a portão único**: sem CF na frente, o `RequireLoginMiddleware` (que guarda **todas** as rotas, ADR-0014/ADR-0036) é a única defesa da superfície pública — o rate-limit do ADR-0014 §9 (`threading.Semaphore(1)`) guarda só o brute-force no `/login`, não a superfície inteira. Em troca, um **ganho honesto**: o TLS termina na própria caixa, matando o MITM-na-borda que o túnel obrigava a aceitar. A regra da security list é **host-wide** (a VCN é compartilhada com os vizinhos do `oute-server`), não escopada ao Kubo.

## Consequências

- **Positivo:** zero migração de DNS; TLS ponta-a-ponta na caixa do dono; `Caddy` versionado no compose migra junto.
- **Trade-off:** porta aberta à internet eleva o peso da auth (ADR-0036 vira crítico); mudança host-wide na security list da VCN.
- **Registrado:** o bloqueio da migração de NS ficou **não-diagnosticado** — importa se a Cloudflare voltar à mesa (ex.: PRD em instância dedicada).

## Alternativas rejeitadas

- **Cloudflare Tunnel (desenho da 0020)** — exige a zona inteira na CF (free) e o dono bateu num bloqueio ao migrar os NS; reabrível só se a PRD sair do LXC para instância dedicada.
- **Tailscale Funnel** — só serve o domínio `ts.net`, morto pelo requisito do domínio próprio (0020).

## Atualização (2026-07-25) — realidade divergente: nginx do host, não Caddy

A exposição foi construída de forma diferente da decidida aqui, e a **premissa central deste ADR era factualmente falsa**.

**Premissa falsa** (§Contexto e §Decisão I, citando KUBO-74/78): *"80/443 fechados no host, nada serve web hoje — abrir a 443 é greenfield, sem proxy existente para conflitar"*. **Errado.** O `oute-server` já rodava **nginx do host** servindo ~13 subdomínios vizinhos (`core`, `dify`, `hermes`, `omnigent`, ...). Não era greenfield: o **padrão estabelecido do host é nginx + certbot por vhost**, e a decisão de Caddy só existiu por desconhecê-lo.

**O que está vivo** (validado 2026-07-25 por SSH ao host + `curl` público):

- `https://kubo.oute.pro` → **nginx do host** (`/etc/nginx/sites-enabled/kubo.oute.pro`), TLS **Let's Encrypt via certbot** (HTTP-01, webroot `/var/www/certbot`), redirect `80 → 443` (301) — tudo *managed by Certbot*.
- Upstream `proxy_pass http://10.173.117.21:2900` — o **kubo-prd** (IP confirmado por `lxc list`, não inferido) pela `lxdbr0`. O nginx já injeta `X-Forwarded-Proto $scheme`, `X-Real-IP` e `X-Forwarded-For`.
- **Caddy não existe** no host (serviço inativo, ausente do compose).

**Consequências da divergência:**

- A propriedade que justificava o Caddy — *"container no compose, portável, migra com `docker compose up`"* — **cai**. O vhost é **artefato manual no host**, fora do repo/compose, **compartilhado com os 13 vizinhos** (config e destino-de-falha comuns; a security list host-wide do §III já antecipava esse compartilhamento). A esteira de CD (ADR-0037) sobe o compose e **não toca o nginx**: o vhost é operado à parte e o certbot renova sozinho. **IP acoplado:** o upstream `10.173.117.21` é **fixado** (netplan + lease estático do LXD, runbook §5.1); como o vhost o hardcoda e o CD não o atualiza, uma troca de IP do kubo-prd quebra a exposição (502) e exige editar o vhost à mão — manter o IP pinado (ou trocar por MagicDNS estável). **Dívida de doc:** o setup do vhost não está no `docs/runbook-deploy.md` (a §5 só cobre a tailnet 2900) — entra na Fatia B.
- As pré-condições do §II seguem **válidas e pendentes no código**: cookie `Secure=True` global e `--forwarded-allow-ips` confiando a origem do nginx no uvicorn são critério de aceite da Fatia B (auth Firebase), não estão feitos.
- O risco do §III (*"a exposição eleva a auth a portão único"*) **materializou-se pior que o previsto**: a superfície pública está guardada hoje por **scrypt de senha única** (Firebase/ADR-0036 ainda não no ar). Gate interino barato: `auth_basic` no vhost fecha a superfície sem esperar código — recomendado enquanto a Fatia B não entra. Se adotado, as credenciais do `auth_basic` seguem o invariante 8 — fora do repo, em secret manager/keychain, referenciadas por config (nunca valor em doc/commit), e removidas quando o Firebase entra.

**Por que a realidade é preferível:** host-nginx+certbot é o padrão que os 13 projetos já usam — consistente, certbot já operado, zero container novo, coerente com a fadiga-de-complexidade. A decisão de Caddy foi correta *dada a premissa*, e a premissa é que estava errada.
