# Sessão 0009 — notas de execução

> Contrato consumido: [`0009-ui-fundacao.md`](0009-ui-fundacao.md). Ambiente: Claude Code CLI (Opus + advisor Fable 5 via subagent `fable-advisor`).

## Entregue

| Marco | Estado | Commit |
|---|---|---|
| 9.1 ADR-0014 esqueleto + deps + scaffold kubo/api | ✅ | `docs: ADR-0014 skeleton`, `feat: scaffold kubo/api` |
| 9.2 Auth (login scrypt, sessão, guard, TrustedHost, hashpw) | ✅ | `feat: browser auth`, `fix: harden login from security review` |
| 9.3 Store `list_distilled(limit, start)` (TDD) | ✅ | `feat: store list_distilled` |
| 9.4 Fatia Destilados (lista, busca, detalhe, XSS testado) | ✅ | `feat: Destilados slice` |
| 9.5 Compose `kubo-api` + Dockerfile stage Tailwind | ✅ | `feat: kubo-api compose service + Dockerfile Tailwind stage` |
| 9.7 Painel (contagens + últimas runs por error.kind) | ✅ **não cortado** | `feat: Painel` |
| 9.8 ADR-0014 final + notas + PR | ✅ | este commit |
| **9.6 Deploy kubo-test + smoke de browser** | ⏳ **GATED** | aguarda "pode executar" do dono |

O timebox segurou — o Painel (1º da fila de sacrifício) foi entregue inteiro, não como stub. Paginação ficou prev/next sem total (corte pré-declarado nº 2, aceito) via truque de pedir `PAGE_SIZE+1`.

## Transparência de custo (checkpoint CLAUDE.md)

- **Delegações:** `doc-writer` (Haiku) → draft do ADR-0014; `security-reviewer` (Sonnet) → review do auth (3 achados, todos endereçados). O resto (scaffold, auth, store reads, fatia Destilados, Painel, compose/Dockerfile) foi inline na thread (Opus) — a fatia é altamente acoplada e security-crítica; o advisor recomendou explicitamente economizar o round-trip test-writer→implementer no auth e não delegar o base.html a Haiku.
- **Consultas ao advisor (Fable 5):** (1) antes de fixar a abordagem de execução — devolveu 6 correções concretas (rotas síncronas, scrypt params empíricos, TrustedHost vs testserver, itsdangerous, ordem de sacrifício, StaticFiles dir); (2) validação do ADR final (este marco).

## Decisões emergentes incorporadas (não estavam no plano, viraram ADR)

1. **Rotas síncronas (`def`)** — o SDK surrealdb é bloqueante; scrypt+sleep em `async` congelariam o event loop de 1 worker. Starlette roda `def` em threadpool.
2. **Gate `Semaphore(1)` no login** — achado do security-review: o `sleep(1)` sozinho não limita concorrência e prende threads (self-DoS). Gate não-bloqueante → 429 rápido, rate-limit real.
3. **Guard de esquema de URL** em `item.url` coletado — achado do próprio 9.4: `javascript:` num href é XSS que o autoescape não pega.
4. **`verify_password` fail-closed** — derive dentro do try/except (param de custo fora de faixa → False, não 500).
5. **`list_distilled`/`recent_runs`: LIMIT/START e ORDER BY** — SurrealDB v3 não aceita bind em LIMIT (interpola int clampado) e exige o campo do ORDER BY na projeção.
6. **Publish do compose** — `kubo-api` sem `ports` na base (PRD/OCI); o overlay `compose.dev-lxc.yml` publica no IP de bridge do LXC.
7. **Correção da E2 (achado empírico do 9.6, validado pelo advisor):** o `100.66.254.24` é o `tailscale0` do HOST, não existe dentro do LXC — o publish direto do compose (E2 original) é impossível. O bind Tailscale-only passa a ser um **LXD proxy device no host** (`listen=100.66.254.24 connect=10.173.117.18 nat=true`); o compose publica em `10.173.117.18:3900` (bridge, interno). Emenda no ADR-0011 §III + runbook §2b. Critério de aceite do `ss` atualizado: com `nat=true` o host **não** tem listener em 3900 (DNAT de kernel) — o smoke vira "curl da tailnet OK + curl do IP público FALHA + nada em 0.0.0.0". Decisão do dono: proxy device, dono roda o comando no host.

## Pendências para a sessão 0010

- **9.6 (deploy + smoke), em andamento:** `uname -m = aarch64` confirmado (pin OK); branch rsyncada; compose corrigido (bridge IP) + proxy device documentado. Bloqueios abertos com o dono: (a) `KUBO_PASSWORD_HASH`/`SESSION_SECRET` ainda não estão no `.env` do servidor (o `:?` barrou o build); (b) o dono roda o `lxc config device add … kubo-ui proxy nat=true` no host. Depois: build → up → smoke (login → Destilados → busca PT-BR → proveniência → logout; curl da tailnet OK, curl do IP público FALHA, `ss` sem 0.0.0.0; reboot religando via proxy device + `restart: unless-stopped`).
- **Telas restantes do D13:** Entidades, Fontes, Execuções (a nav só renderiza o implementado — sem link morto).
- **View toggle D13b** (Lista / Duas colunas / Quadrados).
- **Total na paginação** (hoje prev/next sem total).
- **Usuário read-only do SurrealDB** para a kubo-api (dívida R4 registrada no ADR-0014) — hoje compartilha a credencial root.
- **Markdown nos summaries** (E1) — só numa sessão própria com sanitizador; hoje é escopo negativo.
- **Tripwire de CSRF:** a primeira rota POST além de `/login` exige token CSRF antes do merge.

## Critérios de aceite — status

- [x] Vertical Destilados (lista → busca → detalhe com proveniência) provada localmente contra SurrealDB real (seed + render).
- [x] Guard: sem cookie, tudo (exceto `/login`, `/healthz`, `/static/`) redireciona; senha errada tem sleep + log; concorrência recusada com 429.
- [x] Autoescape testado via rota real (payload `<script>` específico) em lista, partial de busca e detalhe + URL `javascript:` neutralizada.
- [x] Degradação da busca (sem GEMINI_API_KEY → alerta tinted, browse navegável) testada.
- [x] CSS Tailwind standalone pinado + SHA256, stage buildado nativamente em arm64; tokens v2 (stone, pílula, ring, Inter self-hosted, sakura no header).
- [x] ADR-0014 final validado pelo advisor; cobertura ≥85% mantida.
- [ ] **Smoke de browser via tailnet + `curl /healthz` de fora + `ss` do bind** — no 9.6 (gated).
- [ ] Reboot do container religando — no 9.6 (gated).
