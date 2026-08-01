# ADR-0045 — Módulo de perfil de usuário: `user_profile` e aparência por `membership`

> Status: proposto · Data: 2026-07-31

## Contexto

O Kubo tem autenticação multiusuário (ADR-0041) e tenancy (ADR-0039). Até aqui, a tabela `user`
carrega identidade de login (`firebase_uid`, `email`, provedores), `membership` é a relação
`user -> tenant` com papel, e `work_context` descreve o mundo profissional para prompts. Não havia
um lugar para identidade visível e preferências do usuário — nome de exibição, avatar, idioma,
timezone, aparência da UI. O módulo de perfil nasce para preencher essa lacuna sem misturar login,
workspace e contexto profissional.

## Decisão

### I. Conta continua `user`

Login, `firebase_uid`, `email` (só leitura, reflete o provedor), scrypt, provedores e `membership`
ficam onde estão. `user` não ganha campos de perfil.

### II. Tabela/edge `user_profile`

Nova entidade ligada 1:1 a `user`, global por pessoa. Campos:

- `display_name`: string 1–64, não vazia, trim, não única.
- `language`: BCP 47 (ex. `pt-BR`, `en-US`).
- `timezone`: IANA (ex. `America/Sao_Paulo`).
- `user`: referência a `user`.

`avatar` não é armazenado no banco. O frontend computa
`https://www.gravatar.com/avatar/<md5(email)>?d=identicon` a partir do `email` do `user`.

### III. Aparência (`theme`) fica no `membership`

`theme` (`light` | `dark` | `system`, default `system`) é uma preferência por-tenant, vinculada à
relação `membership`. Um mesmo `user` pode ter aparências diferentes em tenants diferentes.

### IV. Escopo fora do módulo de perfil v1

- Troca de senha/scrypt fica fora — continua gerida por scripts de ops/env.
- Gerenciamento de sessões ativas fica fora.
- Notificações fica fora.
- E-mail editável fica fora — o e-mail é lido do provedor de identidade.

## Consequências

**Positivas.** Separação clara entre identidade de login (`user`), identidade visível
(`user_profile`) e preferências de workspace (`membership`); termos adicionados ao `CONTEXT.md`;
modelo mínimo, sem migrations pesadas.

**Trade-offs.** `theme` por-tenant é menos comum no mercado do que global; se a prática mostrar
que as pessoas querem a mesma aparência em todo lugar, pode migrar para `user_profile` no futuro.

**Negativas.** Gravatar introduz dependência externa para avatar, mas o fallback `identicon` dá
cara sempre. BCP 47 e IANA exigem sanitização/validação na entrada.

## Alternativas rejeitadas

- **`avatar_url` no banco com upload de arquivo** — exigiria bucket e sanitização; Gravatar resolve
  o v1 sem infra nova.
- **`theme` global em `user_profile`** — menos flexível no cenário multi-tenant; fácil de mudar
  depois se necessário.
- **`first_name` + `last_name`** — `display_name` é suficiente e segue o padrão moderno
  (GitHub, Notion, Linear).
- **E-mail editável dentro do Kubo** — furaria o provedor de identidade e exigiria re-verificação.
