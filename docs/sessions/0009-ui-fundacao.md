# Sessão 0009 — Fase 2 kickoff: fundação da UI

> **Status:** aprovado pelo dono (2026-07-11, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas (stop-loss) — advisor estima 10–12h de escopo total; os cortes estão PRÉ-DECLARADOS abaixo, sem culpa
> **Estrutura:** 1 PR — branch `feat/0009-ui-foundation` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

O grafo ganha rosto: app FastAPI servindo **Destilados** (lista paginada, busca semântica, detalhe com proveniência) e **Painel** (contagens + últimas runs), no design system v2, atrás de login, publicado em `100.66.254.24:3900` (Tailscale-only). Primeira fatia vertical da fase 2 — tudo que o resto do D13 reutiliza (layout, auth, store reads, HTMX, compose, deploy) nasce aqui.

## Decisões do dono

- **D25:** auth de browser = tela de login (senha única, dono único) + cookie de sessão httponly. Senha como **hash em env** (invariante 8). ADR-0014 emenda o ADR-0003: bearer fica **escopado** a futuras rotas `/api/*` — **sem middleware bearer agora** (YAGNI; ADR escopa, código não nasce).
- **D26:** CSS = **Tailwind 4 standalone CLI** (binário, zero Node) como stage do Docker build. Emenda E5: binário **pinado por versão + SHA256 verificado** no Dockerfile; atenção: build no oute-server é **aarch64** (usar linux-arm64). Documentar caminho de dev local no Mac (binário macOS ou CSS via docker).
- **D27:** primeira fatia = Destilados + Painel. Resto do D13 em sessões futuras — a nav renderiza **só o implementado** (zero link morto).

## Decisões fixadas pela consulta ao advisor (GO com emendas E1–E6, todas incorporadas)

- **E1 — Summaries em texto plano escapado, SEM markdown→HTML.** Renderizar markdown exigiria sanitizador (dep nova + decisão própria). Fase 2 = `white-space: pre-wrap` sobre texto escapado, ponto. Melhorar a tela de detalhe com markdown é o caminho natural onde prompt injection de conteúdo coletado vira **XSS armado** — escopo negativo explícito.
- **E2 — O bind Tailscale acontece no PUBLISH do compose**, não no uvicorn: `ports: "100.66.254.24:3900:8000"` (uvicorn escuta 0.0.0.0 dentro do namespace do container — correto e inofensivo). Consequência operacional: **tailscaled precisa estar de pé antes do compose no boot do LXC** (Docker não publica em IP inexistente) — registrar no runbook-deploy; smoke do marco 6 inclui reboot do container verificando que tudo volta.
- **E3 — `TrustedHostMiddleware`** com IP/MagicDNS permitidos — fecha DNS rebinding (o browser do dono está na tailnet e navega na internet pública; é ele o confused deputy). Uma linha, defesa barata.
- **E4 — Hash de senha via `hashlib.scrypt` (stdlib), zero dep de hash.** Formato `salt$hash` hex em env, verificação com `hmac.compare_digest`. Nada de passlib/bcrypt/argon2. Helper de geração `python -m kubo.api.hashpw` documentado no ADR (o dono roda, cola o hash no `.env` — a senha nunca toca código/chat/log).
- **E6 — Busca semântica NÃO pagina.** Busca retorna k=20 (uma página); paginação existe só no browse (`list_distilled`). Elimina re-embed por página e qualquer cache de embedding (complexidade especulativa). Busca é busca, browse é browse.
- **Cookie (a):** sem flag `Secure`, justificado no ADR-0014 — transporte já cifrado pelo WireGuard da tailnet; TLS via tailscale cert seria ops real por ganho marginal. **Pré-condição registrada (padrão ADR-0003):** se a UI um dia for exposta fora da tailnet, TLS + `Secure` viram obrigatórios ANTES do primeiro deploy.
- **CSRF (b):** token = YAGNI enquanto a UI é read-only; o que não é YAGNI: `SameSite=Lax` + `HttpOnly` + **regra "nenhum GET muda estado"**. (`Strict` não: quebraria link externo→UI, ex. notificação Telegram.) **Tripwire no ADR-0014: a primeira rota POST além de `/login` exige token CSRF antes do merge.**
- **Sessão (c):** cookie assinado stateless — `SessionMiddleware` do Starlette + `itsdangerous` (dep minúscula, justificar no PR; assinatura HMAC caseira é onde mantenedor solo cria bug de timing/expiry). `SESSION_SECRET` em env; expiry 7–30 dias; revogação = rotacionar o secret (desloga o único usuário — aceitável por definição).
- **Rate-limit no login (d):** mínimo sem dependência (~15 linhas): scrypt já custa ~100ms/tentativa + `time.sleep(1)` em falha (single-user, bloquear o worker não machuca) + log estruturado de tentativa falhada. Proporcional à ameaça (alguém já dentro da tailnet).
- **HTMX (e):** `htmx.min.js` **vendorado** em `kubo/api/static/` (versão pinada no nome/comentário) — zero CDN em runtime, coerente com Inter self-hosted.
- **Embedding na busca (f):** timeout do embedder pra UI **~10s** (não os 60s default). Degradação: `EmbeddingError/ConfigError` na rota devolve partial HTMX com alerta *tinted* ("busca indisponível") mantendo browse navegável — UI sem GEMINI_API_KEY continua servindo Painel e listas, sem retry automático. Wiring no compose: `GEMINI_API_KEY: ${GEMINI_API_KEY:-}` (sem `:?` — key não é pré-condição de subir). **Cache: não.**
- **Estrutura (g):** `kubo/api/app.py` (factory + middlewares) · `kubo/api/routes/` com um `APIRouter` por domínio do D13 (`auth.py`, `dashboard.py`, `distilled.py`) · `templates/base.html` + diretório por domínio (`templates/distilled/list.html`, `_results.html` partial HTMX, `detail.html`). Nav D13 = **lista de dicts num módulo** (label PT-BR, rota EN, grupo) — dado, não plugin. **Proibições executáveis:** sem auto-registro de rotas, sem classe-base de view, sem macro Jinja antes da 3ª repetição. "View descartável" = apagar `templates/distilled/` + `routes/distilled.py` não quebra nada.
- **Serviço (h):** uvicorn puro, **1 worker** (um usuário; mais workers = memória + bugs de estado escondidos). `restart: unless-stopped`, `depends_on: surrealdb: service_healthy`, mesmo logging rotation do scheduler, env `SURREAL_*` idêntico. `/healthz` sem auth (200 fixo, sem tocar banco) + healthcheck exec-form com python/urllib (imagem slim não tem curl).
- **Idioma:** labels/textos visíveis PT-BR; rotas, identificadores, templates, código EN (regra do design README).

## Marcos (Destilados atravessa PRIMEIRO — se o timebox estourar, o que fica de fora é o cortável)

| # | Marco |
|---|---|
| 9.1 | **ADR-0014 esqueleto** (decisões acima) + deps novas de uma vez (`fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `itsdangerous` — justificadas no PR, lock + pip-audit) + scaffold `kubo/api/` (factory, Jinja **autoescape ON**, `base.html` com tokens v2 + nav D13 do implementado, static com htmx vendorado + CSS) |
| 9.2 | **Auth:** `/login` (form + scrypt + sleep-on-fail + log), cookie de sessão (SessionMiddleware/itsdangerous, SameSite=Lax, HttpOnly, sem Secure), logout, `TrustedHostMiddleware`, guard de auth em tudo exceto `/login` e `/healthz`. Helper `python -m kubo.api.hashpw` |
| 9.3 | **Store, TDD:** `list_distilled(limit, start)` paginado + `dashboard_counts()` (dataclass). Views NUNCA fazem query crua (invariante 2). Se o Painel for cortado, `dashboard_counts` sai junto (não deixar store órfã de view) |
| 9.4 | **Fatia Destilados completa:** lista paginada (prev/next; total é luxo cortável) → busca semântica (partial HTMX, k=20, degradação f) → detalhe com cadeia de proveniência (distilled → item → source → run). Summaries: texto plano escapado (E1) |
| 9.5 | **Compose + Dockerfile:** serviço `kubo-api` (h), publish `100.66.254.24:3900:8000` (E2), stage Tailwind standalone (E5), nota do tailscaled-antes-do-compose no runbook |
| 9.6 | **Deploy kubo-test + smoke de navegador** — **gated no "pode executar" do dono**: login, browse, busca, proveniência, logout; + reboot do container verificando que tudo volta (R1) |
| 9.7 | **Painel** (contagens + últimas runs, discriminando runs por `error.kind` — insumo da mini-sessão pós-M6). **1º da fila de sacrifício:** vira stub "em construção" com a nav de pé |
| 9.8 | **ADR-0014 final (advisor valida antes de cravar)** + notas de execução com pendências pra 0010 |

## Segurança (primeira classe nesta sessão)

- **XSS via conteúdo coletado é A ameaça da UI:** summaries vêm de LLM sobre conteúdo hostil. Autoescape do Jinja ON e **testado de verdade (R5):** teste renderiza via rota real (TestClient) um distilled com payload `<script>` e afirma o escape no HTML final — nada de asserção de config. Guard barato complementar: grep por `|safe` em templates (teste ou hook) — proibição executável. `|safe` PROIBIDO em qualquer campo que tocou coleta.
- **Dívida consciente registrada no ADR (R4):** kubo-api compartilha a credencial root do SurrealDB (padrão do scheduler) — primeira superfície exposta a browser falando com o banco; usuário read-only do Surreal é melhoria futura nomeada, não desta sessão.
- Segredos novos (`SESSION_SECRET`, `KUBO_PASSWORD_HASH`) só por env (invariante 8) — o dono cria no `.env` do servidor via rito de sempre.

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0014 antes de cravar (esqueleto já nasce desta consulta de planejamento).
2. **Extraordinária:** Tailwind standalone não cobrir algo dos tokens v2 (volta ao dono, nunca Node escondido no build); qualquer necessidade de renderizar markdown (E1 — sessão própria com decisão de sanitizador); publish no IP Tailscale falhar por ordem de boot de um jeito que a nota de runbook não resolva.
3. Conclusão da sessão (deliverables salvos antes).

## Tarefas do dono

- Escolher a senha da UI; rodar o helper `python -m kubo.api.hashpw` quando a sessão pedir e colar `KUBO_PASSWORD_HASH` + `SESSION_SECRET` no `.env` do servidor (rito de sempre — agente nunca lê).
- **"Pode executar"** antes do deploy (9.6).

## Ordem de sacrifício (timebox 8h; advisor estima 10–12h — cortar sem culpa)

1. **1º corte:** Painel inteiro vira stub (+ `dashboard_counts` sai da store junto).
2. **2º corte:** paginação vira prev/next sem total.
3. **3º corte:** polish visual (o layout base com tokens fica; refinamento de componente é 0010).
4. **NUNCA cortáveis:** auth completa (login/cookie/TrustedHost/rate-limit mínimo); fatia Destilados com busca + proveniência; publish Tailscale-only; autoescape testado via rota real; ADR-0014.

## Critérios de aceite

- [ ] No browser (via tailnet): login → lista de Destilados → busca semântica em PT-BR retorna hits do acervo → detalhe mostra summary (texto plano) + cadeia de proveniência completa → logout.
- [ ] Sem cookie válido, toda rota (exceto `/login`, `/healthz`) redireciona pro login; senha errada tem sleep + log estruturado.
- [ ] `curl http://100.66.254.24:3900/healthz` responde de fora do container; serviço NÃO escuta em `0.0.0.0` do host (verificar `ss`/`docker ps` — publish só no IP Tailscale).
- [ ] Teste de autoescape via rota real (payload `<script>` escapado no HTML final) + guard de `|safe` passando.
- [ ] Degradação da busca comprovada (sem GEMINI_API_KEY: alerta tinted, browse funciona).
- [ ] Reboot do container: stack volta sozinha (tailscaled → compose), nota no runbook.
- [ ] CSS gerado pelo Tailwind standalone pinado+checksum no build aarch64; layout usa os tokens v2 (stone, pílula, ring, Inter self-hosted, sakura no header).
- [ ] Cobertura ≥85% mantida nos caminhos com gate; ADR-0014 mergeado; PR conforme (CodeRabbit endereçado; squash; main verificado ponta a ponta).
- [ ] Notas de execução com o que ficou pra 0010 (Painel se cortado, Fontes/Entidades/Execuções, view toggle D13b, total na paginação).

## Escopo negativo da sessão

- Markdown→HTML nos summaries NÃO (E1 — é onde injection vira XSS; sessão própria com sanitizador se um dia precisar). `|safe` em campo coletado NUNCA.
- Middleware bearer NÃO (ADR escopa `/api/*` futuro; R3). CSRF token NÃO (tripwire registrado). TLS/tailscale cert NÃO (pré-condição registrada).
- Multiusuário/convidado NÃO. Sessão com estado no banco NÃO. Cache de embedding NÃO. View toggle (D13b) NÃO.
- Telas além de Destilados + Painel NÃO (nav sem links mortos). SPA/JS framework NÃO (HTMX apenas). Node NÃO (nem no build — standalone only).
- Usuário read-only do SurrealDB NÃO (dívida registrada no ADR). Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-11; decisões do dono D25–D27; consulta de validação ao advisor (Fable 5): GO com emendas E1–E6, todas incorporadas — texto plano nos summaries, bind via publish do compose + ordem de boot no runbook, TrustedHost, scrypt stdlib com helper, pin+SHA256 do Tailwind standalone (aarch64), busca sem paginação; cookie sem Secure com pré-condição, SameSite=Lax + tripwire de CSRF, sessão stateless via itsdangerous, rate-limit mínimo sem dep, HTMX vendorado, degradação da busca com partial tinted, estrutura por domínio sem framework caseiro, uvicorn 1 worker + /healthz, timebox tratado como fatia mínima com cortes pré-declarados (Destilados primeiro, Painel é o corte).*
