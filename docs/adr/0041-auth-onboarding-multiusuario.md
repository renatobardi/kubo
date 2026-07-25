# ADR-0041 — Autenticação e onboarding multiusuário

> Status: **aceito** · Data: 2026-07-25 · **Emenda o ADR-0036** (allowlist fail-closed de `uid`→papel único `owner` deixa de reger o login geral) e reabre a alínea (e) do ADR-0014 num sentido mais amplo (self-signup real, não só um provedor de identidade a mais).

## Contexto

Resolve o ticket [KUBO-108](https://oute.atlassian.net/browse/KUBO-108) do mapa wayfinder [KUBO-104](https://oute.atlassian.net/browse/KUBO-104), sobre o schema `tenant`/`user`/`membership` travado no ADR-0039.

O ADR-0036 fixou: Firebase JS SDK (`signInWithPopup`, só Google+GitHub), verificação server-side via `pyjwt` sem segredo, allowlist fail-closed de `uid`→`owner` único, e scrypt (ADR-0014) como break-glass. O pivot multi-tenant (KUBO-104) exige abrir o login pra qualquer pessoa (self-signup), o que muda o que a allowlist significa e reabre "convidado/multiusuário é escopo negativo" (ADR-0036 §IV, que já previu isso como "3º papel = ADR próprio").

Decisões já fechadas no charting de KUBO-104, assumidas aqui: self-signup aberto via Firebase, sem aprovação manual; BYOK obrigatório no onboarding; workspace compartilhado dentro do tenant.

## Decisão

### I. Entrada e verificação — sem mudança de mecanismo

**Mantido do ADR-0036**: Firebase JS SDK com `signInWithPopup`, **só Google e GitHub** — e-mail/senha do Firebase continua **fora**. A razão original de dropar (evitar auto-registro numa allowlist fail-closed) não se aplica mais, mas a razão prática permanece: Google/GitHub já cobrem verificação de e-mail e recuperação de conta, poupando o Kubo de reimplementar isso. Reabrir e-mail/senha, se algum dia necessário por alcance, é ADR pequeno e isolado.

Verificação server-side (`pyjwt` contra chaves públicas do Google, `kid`, RS256 only, fail-closed em `kid`/algoritmo não suportado) **sem mudança** — é independente de quantos tenants existem.

### II. Primeiro login: cria tenant novo, SEMPRE — exceto convite

Toda identidade Firebase nova (sem `user` correspondente no banco) que loga **pelo caminho normal** (sem token de convite na URL) recebe: `user` novo + `tenant` novo + `membership(role=owner)` — vira owner do próprio tenant, imediatamente, sem aprovação (self-signup, já decidido no charting).

**Convite é um fluxo separado, por token explícito**, não por casamento de e-mail no login normal: o owner gera um convite (tabela nova, ver §IV) que produz um link único `/invite/<token>`. A pessoa convidada usa **esse link**, não o login normal, pra entrar — o token resolve direto pra "criar `membership(role=member)` no tenant do convite", nunca cria tenant novo nesse caminho. Login normal (sem token) sempre cria tenant novo, mesmo que o e-mail do Firebase coincida com um convite pendente — dependência cega no casamento de e-mail entre provedores é superfície de spoofing que este ADR evita deliberadamente.

### III. Migração da allowlist do ADR-0036

A allowlist fail-closed de `uid`→`owner` único **deixa de reger o login geral** — substituída pelo mecanismo do §II. Os `uid`s do dono (hoje na allowlist) recebem, na migração (execução, não decisão deste ADR), uma `membership(role=owner)` **pré-criada** apontando pro tenant zero (ADR-0039) — o primeiro login pós-pivot desses `uid`s reconhece a membership existente em vez de cunhar um tenant novo pro dono.

### IV. Convite de equipe — tabela nova

Tabela **`team_invite`** (nome novo, não reaproveita `invite` do ADR-0033, que é destinatário de distribuição Telegram — entidades diferentes): `tenant_id`, `token` (aleatório, alta entropia), `role` (hoje só `member` — convite pra `owner` não existe, é sempre 1 por tenant, ADR-0039), `created_by` (user do owner que convidou), `expires_at`, `status` (`pending`/`accepted`/`expired`/`revoked`). Aceitar o convite: valida `token` + `status=pending` + não expirado, cria `membership(role=member, tenant_id, user_id)` pro usuário que acabou de logar via Firebase pelo link, marca `status=accepted`.

### V. Break-glass scrypt — escopo mantido, não generalizado

O scrypt (ADR-0014, mantido pelo ADR-0036) continua sendo a emergência pessoal do dono, **escopado só ao tenant zero**. Não vira mecanismo de recuperação de conta para outros tenants — um owner de outro tenant que perde acesso ao Google/GitHub não tem break-glass por ora (fica como fog no mapa: "recovery de conta pra tenant não-zero"). Generalizar exigiria desenho de verificação de identidade de terceiros sem depender de Google/GitHub — feature de produto própria, não pedida.

### VI. Papel `superadmin` cross-tenant

Reabre, num sentido novo, o mecanismo de allowlist do ADR-0036 (`uid`→papel, fail-closed, env): um papel **`superadmin`**, separado de `membership` (que é sempre por-tenant), concedido por allowlist de `uid` em env — hoje só o(s) `uid`(s) do dono. `superadmin` dá acesso administrativo cross-tenant (suporte, moderação, debug), fora do modelo normal de `membership`/`tenant_id` — é a única exceção deliberada à regra "toda leitura tenant-scoped exige membership" do ADR-0039 §II, e deve ser tratada como tal no `kubo/store/` (checagem explícita e auditável, não um bypass implícito).

## Consequências

- **Positivo:** self-signup sem fricção (Google/GitHub, sem aprovação) cumpre a decisão de produto do dono sem reinventar gestão de senha.
- **Positivo:** convite por token evita a superfície de spoofing de "casar e-mail no login normal" — mais simples de auditar (1 token = 1 ação).
- **Positivo:** migração do dono pro tenant zero é mecânica e sem ambiguidade — membership pré-criada, sem lógica condicional espalhada tipo "se for o dono, faz diferente".
- **Trade-off:** break-glass só pro tenant zero deixa outros owners sem recovery — aceito como escopo menor deliberado; vira fog.
- **Trade-off:** `superadmin` é a única exceção ao modelo de autorização por `membership` (ADR-0039) — precisa de checagem própria, explícita, no `kubo/store/`, e de disciplina de auditoria (quem usou `superadmin`, quando, pra ver o quê) — fica anotado como fog/hardening (changelog de uso de superadmin), não decidido aqui.
- **Neutro:** este ADR não desenha a UI de convite/aceitação nem a tela de onboarding de BYOK — só o modelo de dados e o fluxo de auth por trás.

## Alternativas rejeitadas

- **E-mail/senha do Firebase reaberto** — razão original (evitar auto-registro) não se aplica mais, mas gestão de senha própria (reset, verificação) é trabalho evitável reaproveitando Google/GitHub.
- **Convite por casamento de e-mail no login normal** — mais "mágico" pro usuário, mas depende de confiar cegamente que o e-mail retornado pelo provedor bate com o do convite; token explícito é mais simples de auditar e mais seguro.
- **Break-glass genérico por tenant** — recovery de conta multi-tenant é feature de produto própria (verificação de identidade sem Google/GitHub); fora de escopo agora.
- **Sem papel `superadmin`** — mais simples e mais fiel ao modelo de isolamento, mas deixaria o dono sem qualquer via de suporte/moderação num sistema de self-signup público — considerado impraticável a médio prazo.
