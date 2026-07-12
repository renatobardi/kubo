# ADR-0014 — Fundação da UI: autenticação de browser da fase 2

> Status: **proposto** · Data: 2026-07-11
>
> (Emenda ao ADR-0003: bearer estático fica escopado a futuras rotas `/api/*`; UI de browser usa autenticação própria.)

## Contexto

A fase 2 do Kubo ([sessão 0009](../sessions/0009-ui-fundacao.md)) expõe um painel FastAPI + HTMX de browser listando Destilados, Painéis e resultados de busca semântica. É a primeira superfície web navegável do projeto — a primeira vez que o SurrealDB é acessível de um cliente HTTP de browser (não apenas de agentes CLI/internos).

O ADR-0003 estabeleceu bearer token estático para futuro acesso via `/api/*` (máquina-a-máquina), combinado com security list da OCI. Esse mecanismo **não aplica a browser:** carriers de cookie são semântica diferente de authorization headers (sessão com estado implícito *vs.* credencial portável); revogação é ordem de magnitude mais operável (rotacionar secret *vs.* invalidar token em uso); e o protocolo de autenticação é distinto. A UI de fase 2 exige decisão própria.

Ameaças primárias da UI: (a) XSS via conteúdo coletado (summaries vêm de LLM sobre dados hostis — prompt injection em conteúdo coletado é de primeira classe); (b) acesso não-autorizado ao painel (dono solo, sem multiusuário, mas rede ainda é aberta em princípio). Defesa: autenticação de browser + autoescape de Jinja + invariante de não deixar markdown→HTML para esta sessão.

Contexto adicional: kubo-api compartilha credencial root do SurrealDB (padrão herdado do scheduler). Usuário read-only é melhoria futura nomeada — fora de escopo desta sessão (R4).

## Decisão

### 1. Bearer estático escopado (emenda ADR-0003)

O token bearer do ADR-0003 fica **reservado a futuras rotas `/api/*`** (orquestrador externo, máquina-a-máquina). **Nenhum middleware bearer nasce agora** — YAGNI (Yet Another You Aren't Gonna Need It; a decisão arquitetural está no ADR, não no código). A UI de browser usa mecanismo distinto.

### 2. Login de senha única, dono único

Tela `/login` (form POST, método POST, sem AJAX) com campo senha única. Senha verificada contra hash em env `KUBO_PASSWORD_HASH` (invariante 8: nunca valor em código/YAML/commit/log). Sem suporte a múltiplos usuários, sem convidados. Se credencial falhar, redireção de volta a `/login` com alerta. Rate-limit mínimo sem dependência (ver item 9).

### 3. Hash de senha: `hashlib.scrypt` (stdlib), zero dependência de hash

Usa `hashlib.scrypt` da stdlib (Python 3.12+). **Parâmetros: n=2^14, r=8, p=1** (n=2^15 estoura o limite de memória do OpenSSL — footgun confirmado em spike). Formato do hash embute os parâmetros: `scrypt$14$8$1$<salt_hex>$<hash_hex>`, permitindo ajustes de custo futuro sem invalidar senhas antigas silenciosamente. Verificação com `hmac.compare_digest` (timing-safe).

**Helper:** `python -m kubo.api.hashpw` (sem argumentos de senha — entrada via stdin/getpass, nunca em argumento). Output é a string de hash completa, que o dono cola diretamente em `.env` como `KUBO_PASSWORD_HASH=...`. A senha **nunca toca código, chat, log ou commit**.

### 4. Rotas de login síncronas, não async

SDK SurrealDB 2.0.0 é bloqueante; store é 100% síncrona. Hash scrypt custa ~50–150ms; rate-limit de falta inclui `time.sleep(1)`. Com route async e 1 worker uvicorn, o `sleep` congela o event loop (auto-DoS congelaria `/healthz`, serviço fica não-responsivo). **Solução:** rotas de login são `def` (síncronas), não `async def`. Starlette roda função síncrona num thread pool (bloqueia thread isolada, não event loop). Custo: thread pooling tem overhead, mas login é operação baixíssima frequência (~1-2 tentativas por semana).

### 5. Sessão stateless via cookie assinado

Cookie de sessão vazio (dados vazios) assinado com `itsdangerous` (lib minúscula, já usada no Starlette `SessionMiddleware`). Não há estado no banco; sessão é válida enquanto o signature é válido. `SessionMiddleware` do Starlette gerencia compressão, assinatura e expiração. `SESSION_SECRET` em env (invariante 8). `max_age` explícito: 7–30 dias (dono escolhe). Revogação = rotacionar `SESSION_SECRET` (desloga o único usuário — aceitável por definição). Cookie `HttpOnly=True` + `SameSite=Lax` (ver item 6).

### 6. Cookie SEM flag `Secure`, com pré-condição registrada

O cookie não leva flag `Secure` porque o transporte já é cifrado pelo WireGuard da Tailscale — TLS sobre TLS é redundante e adicionaria ops (cert + renewal + DNS). **Pré-condição (marcada como invariante do ADR-0003):** se a UI um dia for exposta fora da Tailscale, `Secure=True` + TLS/HTTPS viram obrigatórios **antes do primeiro deploy**. Triagado no pré-merge se houver tentativa de expor a UI em rede pública.

### 7. CSRF: token = YAGNI enquanto read-only; tripwire registrado

Toda rota da UI (exceto `/login` e `/healthz`) é GET até data de decisão desta sessão. GET nunca muda estado (invariante: `idempotent by definition`). CSRF token é defensável quando forma POST existe; agora é especulativo. Defesa atual: `SameSite=Lax` (protege contra attacker-controlled form POST) + `HttpOnly` (protege contra JavaScript) + **regra de código: nenhum GET altera estado**. `Lax` (não `Strict`) porque dono navega em sites da internet pública via Tailscale — quebrar link externo→UI seria UX ruim.

**Tripwire:** a primeira rota POST além de `/login` que tocar estado (mudar, deletar, etc.) **exige token CSRF no formulário** antes do PR ser mergeado. Registrado como checklist no marco 9.8.

### 8. `TrustedHostMiddleware` contra rebinding de DNS

Middleware de uma linha, parâmetros em env: `KUBO_ALLOWED_HOSTS` (lista separada por vírgula, ex. `"100.66.254.24,kubo.tailnet-..."` — MagicDNS da Tailscale). Fecha ataque de rebinding de DNS (confusion entre browser na tailnet navegando internet pública e quem quer enganá-lo a falar com um localhost falso da máquina host). Comparação ignora porta. Se requerimento vier com Host não-permitido, retorna 400. **Custo operacional:** dono insere a lista em env na primeira subida; mudança de topologia exige atualizar.

### 9. Rate-limit mínimo, sem dependência

~15 linhas de código na rota `/login`:
- Scrypt já custa ~100ms/tentativa.
- Falta de senha = `time.sleep(1)` antes de redirecionar.
- Log estruturado (structlog) com `attempt=failed`, `ip`, `timestamp` (subsídio para monitorar brute force).

Proporcional à ameaça: alguém já dentro da Tailscale tentando quebrar a senha do dono. Sem limite por IP (thread é só uma, bloqueio via sleep é suficiente para uso pessoal).

### 10. Dívida consciente: kubo-api compartilha credencial root

A API herdou do scheduler o uso da credencial root do SurrealDB (env `SURREAL_USER`, `SURREAL_PASS`). Primeira exposição dessa credencial a um cliente de browser. **Usuário read-only do SurrealDB com permissões sobre `distilled`, `flow`, `run` é melhoria futura nomeada** — não desta sessão. Registrado como R4 no plano 0009, e fica como achado de segurança no PR (CodeRabbit pode mencionar; dono valida e desloca).

### 11. XSS: ameaça primária, defesa via autoescape + teste real

Conteúdo coletado (HTML de feeds, transcrições) é dado não-confiável. Summaries são gerados por LLM sobre esse conteúdo — prompt injection em conteúdo coletado é ataque de primeira classe neste projeto (agentes executam o que workers coletam). **Autoescape de Jinja: ON, obrigatório, testado.**

Teste deve renderizar via rota real (TestClient do FastAPI, não mock de template) um distilled com summary contendo payload `<script>alert('xss')</script>` e afirmar que a resposta HTML o escapa para `&lt;script&gt;...&lt;/script&gt;`. Asserção de config ("autoescape=True") não é suficiente — testa-se comportamento real.

Guard barato complementar: grep ou hook de linting procura por `|safe` em templates — **proibição executável:** `|safe` NUNCA em campo que tocou coleta (summaries, títulos de distilled, qualquer dado de fora).

**Escopo negativo:** Markdown→HTML em summaries é escopo negativo explícito (E1 no plano). `pre-wrap` em CSS resolve presentação; renderizar markdown exigiria `bleach` + decisão de tags permitidas — sessão separada com sanitizador se um dia necessário.

## Consequências

### Segredos novos em `.env`

- `KUBO_PASSWORD_HASH` — saída do helper `python -m kubo.api.hashpw`, dono roda uma vez.
- `SESSION_SECRET` — string de 32+ bytes (gerado: `python -c "import secrets; print(secrets.token_hex(32))"`), dono escolhe ou gera.
- `KUBO_ALLOWED_HOSTS` (opcional, default vazio = sem validação) — lista de hosts permitidos, ex. `"100.66.254.24,kubo.localdomain"`.

Todos salvos via rito existente em `.env` do servidor (Tailscale-only, não commitado, gerenciado pela OCI).

### Pré-condição registrada

Se a UI sair da Tailscale para rede pública: TLS via cert (auto-renovado, ex. via Tailscale HTTPS ou Let's Encrypt) + `Secure=True` no cookie são obrigatórios **antes do primeiro deploy**. Esta pré-condição bloqueia qualquer PR futuro que mude a topologia sem TLS.

### Tripwire de CSRF

Checklist no ADR 9.8: **a primeira rota POST além de `/login` exige CSRF token antes do merge** — não deixar código vulnerável de lado especulando que "vai ser read-only para sempre". Token nasce de uma linha (Starlette + `CsrfProtectMiddleware` futura, ou caseiro simples).

### Dívida registrada

Usuário read-only do Surreal é achado de segurança (R4 do plano 0009). Impede escrita acidental ou acesso a tabelas internas via bug de SQLi (improvável em query-builder, mas defense-in-depth). Criação: `CREATE USER read_only ON DB PERMISSIONS GRANT SELECT ON TABLE distilled, flow, run;` (escopo futuro, nomeado).

### Entregas de código + helpers

- `kubo/api/app.py` — factory FastAPI com middlewares (CORS permissiva na tailnet, TrustedHost, Session, exception handlers).
- `kubo/api/routes/auth.py` — `/login` GET/POST, `/logout`, guarda de autenticação.
- `kubo/api/hashpw.py` — módulo `__main__` com helper, rodado como `python -m kubo.api.hashpw`.
- `kubo/api/templates/login.html` — form simples, sem JavaScript.
- `tests/api/test_auth.py` — testes de login (falta de senha, senha correta, sessão válida, logout), autoescape via rota real.

### Impacto operacional

- Deploy: dono roda helper uma vez, insere `KUBO_PASSWORD_HASH` e `SESSION_SECRET` no `.env` do servidor.
- Mudança de senha: dono roda helper de novo, atualiza `.env`, faz deploy. Nenhuma automação.
- Revogação (vazamento de cookie): rotacionar `SESSION_SECRET` desloga o dono até próximo login.
- Topologia Tailscale muda (novo IP/DNS): atualizar `KUBO_ALLOWED_HOSTS` no `.env` e redeploy.

## Alternativas rejeitadas

- **(a) passlib, bcrypt ou argon2** — scrypt da stdlib resolve; adicionar dependência por ~15 linhas de trade-off de complexidade vs. benefício marginal viola fadiga de complexidade (CLAUDE.md premissa). Scrypt é suficiente.

- **(b) HMAC caseiro do cookie** — assinatura manual de timestamp + expiração é onde mantenedor solo cria bug de timing ou expiry. `itsdangerous` é 100 linhas de dependência, bem-auditada, usada no Starlette. Retorno seguro pelo custo.

- **(c) Middleware bearer agora** — `/api/*` é futuro (agentes externos via CLI ainda não existe). Bearer exige docstring e rota de teste; nasceria morto (YAGNI). ADR escopa, código não.

- **(d) Renderizar Markdown em summaries** — VULNerável a XSS se sanitizador for negligente. `bleach` é nova dependência + decisão de tags. `pre-wrap` em CSS + texto plano resolve apresentação — sessão futura com risco-benefício revisado.

- **(e) Multiusuário/SAML/OIDC** — manutenção de identidade central para dono solo é anti-premissa. Bearer+password-grant para outro app futuro é cenário coberto por item (c).
