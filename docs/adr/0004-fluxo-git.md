# ADR-0004 — Convenções de fluxo Git

> Status: **aceito** · Data: 2026-07-04

## Contexto

O projeto é solo mas usa PR como registro obrigatório (CLAUDE.md §Fluxo de trabalho). Faltavam convenções enforçadas: nomes de branch eram informais (`feat/`, `fix/`, `chore/`, `docs/` só por texto), o idioma de commits era PT-BR, e não havia gate automático nem template de PR. A sessão 0002 fixa essas convenções antes do primeiro código de produção (spike SurrealDB), para o histórico nascer disciplinado.

## Decisão

1. **Taxonomia de branch:** `(feat|fix|chore|docs|test|refactor|ci)/slug`, `slug` em kebab-case, a partir de `main`. Ex.: `ci/0002-git-flow`, `feat/0002-surrealdb-spike`.

2. **Idioma (D16), a partir da sessão 0002:** mensagens de commit e títulos/descrições de PR em **inglês**, convencionais (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`, `ci:`). ADRs, docs, planos de sessão, reviews do CodeRabbit e conversas seguem em **PT-BR**. Histórico anterior à sessão 0002 não se reescreve.

3. **Duas camadas de enforcement:**
   - **Local (conveniência):** `guard-bash.sh` barra `git switch -c` / `checkout -b` com nome fora da taxonomia. Cobertura deliberadamente parcial (`git branch <nome>` não é parseado).
   - **CI (gate final):** job `pr-conventions` valida nome de branch e título de PR convencional. Só roda em `pull_request` (em push/schedule o contexto de PR não existe). O título é tratado como input hostil — passa por `env`, nunca interpolado no script.

4. **Deleção de branch pós-merge:** nativa do GitHub (`deleteBranchOnMerge`), não script.

5. **Tags/release adiados:** sem convenção de versionamento/release enquanto não existir deploy. Reabrir quando houver CD.

### Limites conhecidos e pontos em aberto

- **Commits individuais na branch são disciplina, não gate.** O CI valida o título do PR, não cada commit. Com squash-only (abaixo), isso deixa de importar em `main`: os commits da branch são descartados no merge e só o squash — cuja mensagem é o título validado — entra. Na branch, a convenção dos commits segue por disciplina.
- **Estratégia de merge — squash-only (decidido pelo dono na sessão 0002):** o merge do PR é sempre por *squash*, usando o título convencional do PR como mensagem do commit em `main`. Assim o único commit que chega a `main` já passou pela validação de título, fechando a lacuna acima (commits individuais não são enforçados). `merge commit` e `rebase` ficam desabilitados no repositório. O `Merge pull request #1…` pré-existente em `main` não se reescreve.
- **Branches de bots (Dependabot/Renovate) usam `/` aninhado** (`dependabot/pip/…`) e falhariam no regex da taxonomia. Hoje não há bot de PR (pip-audit roda no CI, não abre PR); se um entrar, o job `pr-conventions` precisará de exceção.

## Consequências

Histórico legível e verificável; o CI é a verdade final (o guard local é só feedback rápido e pode ser incompleto sem risco). Custo: o CI precisa ler contexto de PR com cuidado contra injeção via título. A divisão de idioma (código/commits em inglês, docs em PT-BR) exige atenção do autor, mas mantém o registro técnico universal e a documentação no idioma do dono.

## Alternativas rejeitadas

(a) Enforce só no CI, sem guard local — rejeitada: feedback tardio (só no PR); o guard local corrige na hora.

(b) Enforce só local (hook) — rejeitada: hook é contornável e não roda para contribuições fora do harness; o CI é o gate confiável.

(c) Ferramenta dedicada (commitlint/husky) — rejeitada: dependência nova (Node) para o que um `grep` resolve; contraria a premissa de fadiga de complexidade.

(d) Manter commits em PT-BR — rejeitada (D16): o registro técnico ganha alcance universal em inglês; docs permanecem PT-BR para o dono.
