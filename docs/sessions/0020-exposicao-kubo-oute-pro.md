# Sessão 0020 — Exposição: kubo.oute.pro na internet

> **Status: ADIADA, aguarda PRD (decidido 2026-07-17).** O desenho abaixo continua fixado
> (fica como registro), mas não roda como está. Motivo: `kubo.oute.pro` pertence ao ambiente
> de produção futuro na OCI, não ao DEV `kubo-test` onde a fase 1 roda hoje (ADR-0011) — expor
> o DEV atual na internet é a fronteira errada. O dono já acessa o Kubo no celular pela tailnet
> (validado e aprovado na 0019, 2026-07-15); o PR2 (`:43`, acesso guest do amigo) perdeu o
> gatilho quando a validação do amigo pela 0019 nunca aconteceu. Reabre quando a PRD OCI
> nascer — recicla o desenho, não repete o planejamento do zero.
>
> **Status original (histórico):** aprovado pelo dono (2026-07-15, planejamento no Cowork); advisor GO com desenho fixado
> **Ambiente de execução:** Claude Code CLI — **sessão de FRONTEIRA** (muda a fronteira de segurança do sistema)
> **Política de modelo (custo-benefício, regra do dono):** Opus na thread até fixar abordagem dos marcos de auth (PR2); implementação sobre desenho validado pode ir a Sonnet com compensações (advisor por marco, checkpoints, travou 2x → para).
> **Timebox:** 8 horas efetivas — **2 PRs** (fatias verticais); PR1 sozinho já entrega o caso de uso principal (dono no celular sem Tailscale)
> **Estrutura:** branches `feat/0020-tunnel-tls` (PR1) e `feat/0020-oauth-guest` (PR2)
> **Contrato:** executa SOMENTE o que está aqui. **Pré-condições: sessões 0018 e 0019 encerradas**; nameservers de `oute.pro` na Cloudflare (verificar ANTES de começar — sem isso o desenho reabre).

---

## Missão

A UI do Kubo acessível como **https://kubo.oute.pro** da internet: você no celular no dia a dia sem Tailscale; teu amigo validando a UI mobile como convidado só-leitura. A tailnet **continua** como caminho primário e escape de lockout. Emenda consciente ao D18/ADR-0011: a fronteira deixa de ser só-tailnet.

## Decisões do dono

- **D46:** OAuth Google E GitHub com **allowlist de 2 contas** (dono + amigo).
- **D47:** convidado = **só leitura** (botões invisíveis + escrita negada); dono opera normal.
- **D48:** expõe o kubo-test atual via **túnel** (sem abrir porta no firewall/roteador); migra junto quando a PRD OCI nascer.

## Desenho fixado pelo advisor

### Transporte (PR1)
- **Cloudflare Tunnel, `cloudflared` como container NO COMPOSE**, apontando para `http://kubo-api:8000` pela rede interna (NUNCA para o IP do LXC nem para o bind tailnet — elimina o hop pela lxdbr0 e não depende do proxy device). Túnel = serviço versionado no compose; migra pra OCI com `docker compose up`, zero estado no host.
- DNS `kubo.oute.pro` na Cloudflare, **sempre proxied** (nuvem laranja) — NUNCA A record pro IP de casa. Token do cloudflared no `.env` (invariante 8).
- **Rejeitados:** Caddy+port-forward (abre 443 no roteador doméstico, expõe IP residencial); Tailscale Funnel (só domínio ts.net — morto pelo requisito do domínio próprio).
- **Risco aceito com nome no ADR:** TLS termina na borda da CF (MITM-by-design) — conteúdo é painel pessoal; o adversário relevante não é a Cloudflare. CF é transporte burro e substituível (trocar por Caddy não toca a auth).

### TLS interno + cookie (PR1, INSEPARÁVEL do túnel)
- Pré-condição do ADR-0014 §6 dispara: **`Secure=True` no cookie é obrigatório** antes do deploy exposto. Mas cookie Secure não trafega em `http://100.66.254.24:3900` — quebraria o login tailnet. Solução: **`tailscale serve`** no host (TLS automático no nome ts.net, um comando, persiste) e `Secure=True` GLOBAL. **Nunca cookie condicional por scheme.**
- **`X-Forwarded-Proto`:** uvicorn com proxy headers confiados SÓ do IP do container cloudflared (`--forwarded-allow-ips`); sem isso o `redirect_uri` do OAuth nasce `http://` e o flow quebra. `kubo.oute.pro` entra em `KUBO_ALLOWED_HOSTS`.

### OAuth (PR2)
- **Authlib** (dependência nova JUSTIFICADA no PR: o código que ela substitui — state, token exchange, validação de id_token via JWKS — é 100% security-critical; madura; integração Starlette; testável com respx).
- **Allowlist por identificador IMUTÁVEL**, nunca só e-mail: Google → claim `sub` (exigir `email_verified=true`); GitHub → `id` numérico (handle é RECICLÁVEL — allowlist por handle = account takeover esperando data). Bootstrap pragmático: primeiro login loga `sub`/`id`, dono pina no env; e-mail vira rótulo humano. GitHub: `/user/emails`, só `verified && primary`.
- `state` random na sessão + `compare_digest`; callback com path fixo, **SEM parâmetro `next`** (mata open redirect por construção); **regenerar a sessão no login** (fixation).
- Papel como claim na sessão itsdangerous EXISTENTE (`role: owner|guest`, `sub`). Zero mudança de mecanismo de sessão.
- **Login scrypt: MANTER como está, sem gating** — escape de lockout com dependências DISJUNTAS (OAuth cai se Google/GitHub/CF caírem; tailnet+scrypt não depende de nenhum). Sessão scrypt mapeia para `role=owner`. Gating por IP atrás de proxy = lixo (XFF forjável) — não fazer.

### Guest read-only (PR2)
- **Middleware deny-by-default por MÉTODO** (não dependency por rota): `role != owner` → qualquer não-GET (exceto /login, /logout, /oauth/*) = 403. Rota POST criada daqui a 6 meses nasce coberta; dependency por rota falha aberto por esquecimento. Jinja esconde botões via `role` (cosmético; o enforcement é o middleware). Reforço estrutural de graça: guest nunca alcança `connect_rw` (só vive em handlers POST, ADR-0018 §I).
- **Guest vê tudo-leitura** (subset por papel = código novo sem valor pro caso de uso), MAS o acesso é **temporário por design**: remove da allowlist (env) depois da validação da 0019. Decisão explícita no ADR, não default acidental.
- Sessão de guest com `max_age` menor (7d); structlog com `role`+`sub` em toda escrita.

### Endurecimento pontual
- **Step-up no "Confirmar promoção"** (materialização do invariante 5): exigir sessão fresca (`auth_time` < N min; velha → redirect pelo OAuth, um clique). ~15 linhas. Aprovar gate/disparar flow ficam como estão (cookie Secure + CSRF + staleness + allowlist bastam; escrita é idempotente/no-op-safe).
- **Gatilho (c) do ADR-0018 dispara e é REACEITO com nome:** kubo-api exposto carrega GEMINI key + TELEGRAM_BOT_TOKEN; mitigação = auth na frente + container isolado; split de processo = decisão futura. Não passa em silêncio.
- `/healthz` público via túnel = vazamento trivial aceito.

## Marcos (2 PRs)

| # | Marco | PR |
|---|---|---|
| 20.1 | **ADR-00XX (exposição) + emenda ao ADR-0011** (fronteira; riscos aceitos nomeados) — ANTES do código; advisor valida os dois quando redigidos | — |
| 20.2 | cloudflared no compose + DNS proxied + allowed_hosts + proxy headers (runbook: criação do túnel, token no .env) | PR1 |
| 20.3 | `tailscale serve` no host + `Secure=True` global (pré-condição ADR-0014) — runbook atualizado (passos one-time entram no runbook-deploy.md, senão restore quebra em silêncio) | PR1 |
| 20.4 | **Smoke PR1 (gated):** dono acessa https://kubo.oute.pro pelo 4G do celular; login scrypt funciona; tailnet segue funcionando via nome ts.net | PR1 |
| 20.5 | OAuth owner: Authlib, 2 providers, allowlist por sub/id, testes respx; registro dos apps OAuth (runbook do dono: console Google + GitHub, redirect URIs) | PR2 |
| 20.6 | Guest role: middleware deny-by-default + templates + max_age + step-up do Confirmar promoção + testes (positivo E negativo: guest tentando POST = 403) | PR2 |
| 20.7 | **Smoke PR2 (gated):** dono loga via Google E GitHub; conta fora da allowlist é negada; amigo entra como guest, navega, NÃO vê botões, POST forjado = 403 | PR2 |

## Critérios de aceite

- PR1: kubo.oute.pro no ar via 4G; nenhuma porta nova no firewall/roteador; tailnet intacta (com TLS via ts.net); cookie Secure global.
- PR2: allowlist por identificador imutável provada (conta estranha negada); guest 100% read-only por middleware (teste negativo); step-up no Confirmar promoção; scrypt fallback vivo.
- Runbook completo (túnel + tailscale serve + registro OAuth); ADR + emenda cravados com advisor.

## Escopo negativo

- NADA de porta aberta no roteador/firewall; SEM Cloudflare Access (estado de segurança fora do repo); SEM RBAC além do binário owner/guest (3º papel = ADR próprio); SEM subset de dados pra guest; SEM remover scrypt; SEM split do kubo-api nesta sessão (reaceite nomeado); SurrealDB continua inalcançável de fora do compose.

## Sacrifícios pré-declarados (ordem)

1. PR2 inteiro (OAuth+guest) → dono usa scrypt no celular por uns dias (TLS + senha forte + rate limit existente); amigo espera.
2. Step-up do Confirmar promoção → vira follow-up imediato.
**Nunca cortar:** 20.3 (TLS/Secure — pré-condição), 20.1 (ADR antes do código).

## Preparos do dono (runbook literal na sessão)

- Nameservers de `oute.pro` → Cloudflare (VERIFICAR ANTES — sem isso, replanejamento).
- Conta Cloudflare (free) + criação do túnel (token → .env).
- Registro dos apps OAuth: Google Cloud Console + GitHub Developer Settings (redirect URIs `https://kubo.oute.pro/oauth/{google,github}/callback`).
- `sub`/`id` do amigo (primeiro login loga, dono pina no env).
