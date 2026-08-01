# ADR-0046 — `work_context` vive em `user_profile` e é editado em `/profile`

> Status: proposto · Data: 2026-08-01

## Contexto

Até a ADR-0043 o `work_context` (texto livre que descreve o mundo profissional do dono e entra em prompts de personas) estava na tabela `user`. Quando o módulo de perfil surgiu (ADR-0045), ficou claro que `work_context` é parte da identidade visível/preferências globais de uma pessoa, não de sua identidade de login. Manter o campo em `user` forçava a tela de Configurações a editar um dado pessoal que não é configuração operacional.

## Decisão

### I. `work_context` muda para `user_profile`

O campo passa a ser `work_context` na tabela `user_profile`, com cardinalidade 1:1 com `user`. A migration 0028 remove o campo de `user` e o adiciona em `user_profile`; dados antigos em `user` não são migrados (são descartados).

### II. Edição unificada em `/profile`

`GET/POST /profile` passam a tratar `display_name`, `language`, `timezone` e `work_context` num único formulário. A rota `POST /settings/profile` é removida e a seção "Contexto de trabalho" some da tela de Configurações.

### III. Validação

- `display_name` continua obrigatório (1–64 caracteres).
- `work_context` é opcional; string vazia limpa o campo (grava `None`).
- `work_context` tem teto de 4000 caracteres, validado tanto no `ProfileForm` da rota quanto na store (`update_user_profile`).

### IV. Revisão com IA

A tela de perfil ganha um botão "Revisar com IA" ao lado do campo `work_context`. O botão chama `POST /profile/work-context/review` (síncrono), que usa uma persona `work_context_reviewer` do catálogo default (`anthropic/claude-haiku-4-5`) para revisar o rascunho e devolver JSON `{"work_context": "..."}`. A rota:
- aplica o mesmo teto de 4000 caracteres antes de chamar o LLM;
- carrega a persona direto de `DEFAULT_PERSONAS`, sem passar pelo `resolve_persona` do tenant, para garantir que o dono não a edite ou substitua;
- **não semeia** a persona no catálogo do tenant (`_NON_SEEDED_PERSONAS` em `store/catalog.py`): se fosse semeada, apareceria editável na UI mas edições não teriam efeito, contradizendo o bypass acima;
- falhas do executor viram resposta 503 (padrão herdado da ADR-0032 para LLM síncrono em rota).

## Consequências

**Positivas.** Separação correta entre identidade de login (`user`) e perfil pessoal (`user_profile`); uma única tela de edição de perfil; o contexto de trabalho passa a ser editável junto com as outras preferências globais; a revisão com IA dá feedback imediato sem persistir até o dono salvar.

**Trade-offs.** Dados antigos de `work_context` são perdidos; se a produção já tiver contextos preenchidos, será necessário preenchê-los novamente (escolha aceitada por simplicidade, dado o estágio do projeto).

**Negativas.** A revisão com IA é síncrona e depende da disponibilidade do provider; falhas retornam 503 e a UI trata com alerta. A persona de revisão é obrigatória no catálogo default.

## Alternativas rejeitadas

- **Migrar `user.work_context` para `user_profile`** — descartada para evitar complexidade de backfill entre tabelas; dados antigos são pequenos e a reescrita manual é aceitável.
- **Deixar `work_context` editável em Configurações** — manteria o dado pessoal fora do seu lugar semântico.
- **Usar `resolve_persona` para a persona de revisão** — permitiria que um tenant com uma persona homônima sobrescrevesse o revisor do sistema, violando "não editável pelo dono".
