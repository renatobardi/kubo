# ADR-0036 — Autenticação da PRD: Firebase (Google + GitHub via SDK) + scrypt break-glass

> Status: **aceito** · Data: 2026-07-22 · **Revoga o ADR-0014 §"Alternativas rejeitadas" (e)** ("Multiusuário/SAML/OIDC — anti-premissa"). Emenda o desenho de auth da sessão 0020.

## Contexto

A PRD é exposta à internet por porta-aberta (ADR-0035), o que torna a **autenticação o portão único** do sistema — não há mais uma borda Cloudflare na frente. O dono decidiu (D-b) usar **Firebase Auth**, com o trade-off à vista (vendor de identidade + algum JS num app deliberadamente HTMX-sem-JS, contra o Authlib direto que a 0020 desenhou).

O **ADR-0014** rejeitou explicitamente, na alternativa (e), *"Multiusuário/SAML/OIDC — manutenção de identidade central para dono solo é anti-premissa"*. Este ADR **revoga** essa alternativa: um provedor de identidade (Firebase) entra, com o custo nomeado. Pesquisa e desenho em [KUBO-75](https://oute.atlassian.net/browse/KUBO-75) e [KUBO-79](https://oute.atlassian.net/browse/KUBO-79).

## Decisão

### I. Entrada: SDK JS `signInWithPopup` só no login (Google + GitHub)

O **Firebase JS SDK** com `signInWithPopup` roda **só na tela de login** (o resto do app segue HTMX puro). O Firebase conduz o OAuth de Google e GitHub; o cliente recebe o **ID token** e o envia por POST ao servidor. **`signInWithPopup`, nunca `signInWithRedirect`** — o redirect quebra sob particionamento de storage de terceiros (Chrome/Safari/Firefox) e exigiria `authDomain` em domínio próprio.

**E-mail/senha do Firebase é DROPADO.** O scrypt (§III) já é o login por senha; ligar e-mail/senha no Firebase tornaria o projeto **auto-registrável** (qualquer `signUp` → ID token válido) — superfície de auto-cadastro no portão único, redundante com o scrypt. O Firebase faz **só Google + GitHub**.

### II. Verificação no servidor: `pyjwt`, zero segredo

O servidor verifica o ID token com **`pyjwt`** contra as chaves públicas do Google (`aud` = project id, `iss` = `securetoken.google.com/<pid>`, `exp`, `sub` não-vazio, **`email_verified=true`**). **Não** usa `firebase-admin` (traz gRPC/Firestore, ~40–60 MB) nem `createSessionCookie` (exige service account em disco → furaria o invariante 8). Verificar exige só chave pública + project id — nenhum segredo.

No sucesso, emite o **cookie `itsdangerous` existente** (`role=owner` + `uid`), com `Secure=True` global (ADR-0035) e **regeneração de sessão no login** (fixation). O CSRF do POST do ID token é auto-mitigado (o token no corpo é a prova; não se forja ID token válido para o `uid` do dono).

**Semântica de revogação:** remover uma identidade da allowlist só morde no **próximo login**; cookie existente vale até `_SESSION_MAX_AGE`. Kill imediato = rotacionar `SESSION_SECRET`.

### III. Allowlist FAIL-CLOSED + scrypt break-glass

- **Allowlist por identificador imutável** = o `uid` do Firebase, vivendo em **env** (invariante 8; sem caminho de escrita de auth no banco). **FAIL-CLOSED:** allowlist vazia ou malformada **nega tudo**, nunca libera — é o portão único. Bootstrap: primeiro login loga o `uid`, dono pina no env. Mapeia as identidades do dono (Google + GitHub) → `owner`.
- **Login scrypt (ADR-0014) MANTIDO** como break-glass — dependência **disjunta** (nenhum vendor no caminho), alcançável pela tailnet (cookie `Secure` via `tailscale serve`). É o único caminho que não depende de Firebase/Google/GitHub; escape de lockout de um mantenedor solo cujo portão único é um vendor.

### IV. Papéis, endurecimento e superfície

- **Papel único (`owner`).** Convidado/multiusuário é escopo negativo (3º papel = ADR próprio; o gatilho do convidado da 0020 morreu). **Middleware deny-by-default por método** mantido como defesa estrutural (rota POST futura nasce coberta).
- **Step-up no "Confirmar promoção"** (materialização do invariante 5): exige sessão fresca; com o popup, re-auth é um re-popup.
- **`kubo-api` carrega só os segredos que usa** (auditar env no build; se não chama LLM, a chave sai) — mitigação barata de superfície. O **split de processo completo** (separar o tier web exposto do tier de segredos) fica adiado como ADR próprio.

### V. Gatilho de reabertura (verificar antes)

`signInWithPopup` no **celular do dono** é a premissa load-bearing (o uso primário é o telefone). Popup tem modos de falha em mobile (bloqueio no iOS Safari, o dance de `postMessage`). **Smoke obrigatório cedo: popup no aparelho real, pelo caminho público do Caddy.** Se falhar, os fallbacks são os dois já rejeitados (redirect exige `authDomain` próprio; REST = hand-roll dos dois OAuth) → **reabre a decisão**.

## Consequências

- **Positivo:** o dono não gerencia hash de senha de terceiros; MFA/recuperação ficam nas contas Google/GitHub (onde devem estar).
- **Trade-off:** dependência de vendor (Google) no caminho principal de auth — mitigada pelo scrypt disjunto como break-glass; JS mínimo entra na tela de login de um app HTMX.
- **Segredo no vendor:** o client secret do GitHub OAuth vive no console do Firebase (fora de repo/código — adjacente ao invariante 8). `kubo.oute.pro` precisa entrar em Authorized Domains do Firebase no build.
- **Revoga** o ADR-0014 (e); o bearer estático do ADR-0003 segue escopado a futuras rotas `/api/*`.

## Alternativas rejeitadas

- **Authlib direto (desenho da 0020)** — o dono escolheu Firebase (D-b); reconfirmado à luz de KUBO-75 (zero-JS via Firebase = hand-roll dos dois OAuth + o vendor).
- **Firebase REST server-side (zero JS)** — hand-roll do OAuth de Google e GitHub no servidor (mais código security-critical no portão único).
- **`firebase-admin` / `createSessionCookie`** — gRPC/Firestore + service account em disco (fura o invariante 8).
- **E-mail/senha do Firebase** — torna o projeto auto-registrável e é redundante com o scrypt.
