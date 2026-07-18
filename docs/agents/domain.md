# Domain Docs

Como as skills de engenharia devem consumir a documentação de domínio deste repo ao explorar
o codebase.

## Antes de explorar, leia

- **`CONTEXT.md`** na raiz do repo, ou
- **`CONTEXT-MAP.md`** na raiz, se existir — aponta para um `CONTEXT.md` por contexto. Leia
  cada um relevante ao tópico.
- **`docs/adr/`** — leia os ADRs que tocam a área em que você vai trabalhar.

Se algum desses arquivos não existir, **prossiga em silêncio**. Não sinalize a ausência; não
sugira criá-los de antemão. A skill `/domain-modeling` (alcançada via `/grill-with-docs` e
`/improve-codebase-architecture`) os cria de forma preguiçosa, quando termos ou decisões são
de fato resolvidos.

Nota Kubo: as fontes de verdade de escopo/conceitos hoje são `docs/kubo-spec-funcional.md`
e `docs/kubo-design-system.md` (ver CLAUDE.md). `CONTEXT.md` não existe ainda — e tudo bem.

## Estrutura de arquivos

Repo single-context (a maioria — inclui o Kubo):

```
/
├── CONTEXT.md           (ausente por ora; criado sob demanda por /domain-modeling)
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
└── kubo/
```

Repo multi-context (presença de `CONTEXT-MAP.md` na raiz): um `CONTEXT.md` por contexto sob
`src/<contexto>/`, com `docs/adr/` global para decisões de sistema. **Não é o caso do Kubo.**

## Use o vocabulário do glossário

Quando o output nomear um conceito de domínio (título de issue, proposta de refactor, hipótese,
nome de teste), use o termo como definido em `CONTEXT.md` — ou, no Kubo, como definido na spec
funcional e no schema (nomes de tabela/aresta em inglês, exatamente como na spec). Não derive
para sinônimos que o glossário evita.

Se o conceito ainda não está no glossário, isso é um sinal — ou você está inventando linguagem
que o projeto não usa (reconsidere), ou há lacuna real (anote para `/domain-modeling`).

## Sinalize conflitos com ADR

Se o output contradiz um ADR existente, exponha explicitamente em vez de sobrescrever em
silêncio:

> _Contradiz ADR-0007 — mas vale reabrir porque…_
