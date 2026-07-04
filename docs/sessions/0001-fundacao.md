# Sessão 0001 — Fundação de governança (M1)

> **Status:** aprovado pelo dono (2026-07-04, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 2 dias de trabalho efetivo
> **Contrato:** esta sessão executa SOMENTE o que está neste documento. Qualquer coisa fora dele reabre a etapa de planejamento (CLAUDE.md, "Modo de trabalho").

---

## Missão

Levar o repo de "initial commit" a fundação completa e verde: documentos canônicos commitados, harness funcional e corrigido, toolchain pinada, Docker Compose com SurrealDB + backup — **zero código de produção**. O harness e o CI ficam comprovadamente verdes ANTES de existir código que eles deveriam proteger.

## Contexto — Fase 1 replanejada em 6 marcos

O plano preliminar de 4 marcos foi refinado na sessão de planejamento (com advisor) para 6. Esta sessão cobre o M1; cada marco seguinte terá seu próprio plano em `docs/sessions/`:

1. **M1 — Fundação de governança** (esta sessão): docs, harness corrigido, pins, compose + backup.
2. **M2 — Spike SurrealDB**: teste de integração exercitando document + graph (RELATE/travessia) + vector (HNSW) + SDK async. Outputs: ADR de pin de versão do SurrealDB, decisão de modelo de embedding (dimensão), esqueleto do runner de migrations. Se o spike revelar fragilidade séria, a aposta do banco reabre ANTES da store existir.
3. **M3 — Schema de conhecimento + store**: migrations do schema de conhecimento (spec §2.3) + tabela `run` + camada `kubo/store/` por TDD (cobertura 85% enforçada a partir daqui).
4. **M4 — Contrato de worker + catálogo**: ADR do contrato (decisões já tomadas, Apêndice A) + ADR anti-injection + loader do catálogo `integrations` (somente ele).
5. **M5 — Porte do feed + APScheduler**: primeira coleta agendada real ponta a ponta, registrada em `run`.
6. **M6 — Destilação + CLI de consulta**: worker de destilação (LiteLLM) + `kubo query`/`kubo show --provenance` → **prova dos 90 dias como teste de aceitação executável**. Harvest logo atrás; scribe é stretch goal.

## Pré-requisitos (antes de abrir a sessão no CLI)

Os artefatos da sessão de planejamento existem só como anexos de conversa. A sessão CLI é fria: só enxerga o repo. Portanto:

1. O dono coloca os 4 artefatos em **`/Users/bardi/Projects/Github/kubo/planning/`** (diretório de staging, temporário), com exatamente estes nomes:
   - `CLAUDE.md`
   - `kobo-spec-funcional.md` (será renomeado para `kubo-spec-funcional.md` — grafia "Kobo" no conteúdo também será corrigida)
   - `kobo-design-system.md` (idem, para `kubo-design-system.md`)
   - `kubo-harness.zip` (contém `.claude/hooks/`, `.claude/agents/`, `.claude/settings.json`, `.coderabbit.yaml`, `.github/workflows/ci.yml`)
2. `planning/` não entra em commit: a sessão o consome (move/renomeia/extrai) e o remove ao final.
3. Passo 0 da sessão: inspecionar o zip e confirmar o inventário acima antes de qualquer marco.

### Tarefas do dono (fora do alcance dos agentes)

- Instalar o app do CodeRabbit no repo `renatobardi/kubo` (GitHub App), se ainda não instalado.
- Configurar branch protection de `main` (PR obrigatório) no GitHub.
- Providenciar (quando for fazer o deploy real, não nesta sessão): bucket OCI Object Storage + credenciais para o backup. Nesta sessão o upload fica apenas parametrizado.

## Marcos e delegações

### 1.1 — Esqueleto e docs (subagent `scaffolder`, Haiku)

- Estrutura de diretórios conforme CLAUDE.md ("Estrutura do repositório"): `docs/adr/`, `docs/sessions/`, `catalogs/{integrations,personas,flow_templates}/`, `kubo/{api,store,runtime,executors,workers,distribution,contracts}/`, `tests/`.
- Módulos Python vazios com docstring de propósito (PT-BR) — nenhuma lógica.
- Docs canônicos movidos de `planning/` para `docs/`, renomeados para `kubo-spec-funcional.md` e `kubo-design-system.md`; `CLAUDE.md` na raiz. Conteúdo: toda ocorrência de "Kobo"/"kobo" vira "Kubo"/"kubo" (o nome canônico é decisão do dono — Apêndice A, D1).
- `pyproject.toml` (uv; `requires-python` exato 3.12; deps mínimas de tooling: ruff, pyright, pytest, pytest-cov, detect-secrets, pip-audit como dev-deps) + `.python-version` + `uv.lock` commitado.
- **Reconciliar** o `.gitignore` existente (~4,6 KB já commitado) — não criar/sobrescrever: acrescentar `planning/`, `.env*` (exceto `.env.example`), artefatos de build.
- `docker-compose.yml`: SurrealDB com tag de imagem pinada (pin provisório; o definitivo sai do spike M2) + serviço de backup: `surreal export` diário via cron, dump local em volume, upload para OCI Object Storage **parametrizado via `.env.example`** (nunca credencial commitada).
- Template de ADR em `docs/adr/template.md` (formato curto do CLAUDE.md: Contexto / Decisão / Consequências / Alternativas rejeitadas) + `docs/adr/README.md` como índice.
- Teste de sanidade `tests/test_sanity.py`: importa `kubo`, verifica versão. (Exceção deliberada ao escopo negativo — sem ele, `pytest` retorna exit code 5 com zero testes coletados e o CI fica vermelho por definição.)

**Proibido ao scaffolder:** `.claude/`, `.github/`, `.coderabbit.yaml` (caminhos sensíveis — ver 1.2).

### 1.2 — Harness, CI e review config (thread principal, Opus)

`.github/workflows/` executa código com acesso a secrets — tão sensível quanto `.claude/`. Fica com a thread principal:

- Extrair o harness do zip para o repo; corrigir bugs conhecidos:
  - `stop-tests.sh`: usa flag pytest inexistente (`--deselect-on-failure`) e executa a suite até 3×. Reescrever para 1 execução, capturando output.
  - Revisar os 4 hooks contra a documentação atual de hooks do Claude Code antes de commitar.
- `ci.yml`: instalar do zip; ajustar para que o gate de cobertura fique **configurado mas não enforçado** nesta fase (com módulos vazios, `--cov-fail-under=85` falha por "no data" ou passa vacuamente). Marcar com comentário: enforce a partir do M3. NUNCA remover o gate — apenas adiar o enforce.
- `.coderabbit.yaml`: instalar do zip, sem alterações de conteúdo.
- Gerar `.secrets.baseline` (`detect-secrets scan`).

### 1.3 — ADRs (subagent `doc-writer`, Haiku → advisor valida)

Um ADR por decisão (disciplina desde o primeiro):

- **ADR-0001 — Nome canônico "Kubo"** (um parágrafo; decisão do dono, registrada).
- **ADR-0002 — Tabela `run` como extensão consciente da spec**: log estruturado de execução na fase 1; `distilled -[produced_by]-> run` até a fase 3 religar a flows por migration. Contenção registrada: uma terceira tabela extra-spec é sinal de scope creep e para tudo.
- **ADR-0003 — Auth da API: bearer token estático** + security list OCI. (Adiável: se o timebox apertar, cai para a sessão que expuser API.)

Drafts do doc-writer; **decisão validada pelo advisor antes de cravar** (regra do CLAUDE.md); registro final da thread principal.

### 1.4 — PR de fumaça (thread principal, Opus)

- Branch `chore/0001-fundacao`, PR para `main`.
- **Evidência de disparo dos hooks (critério objetivo):** documentar no corpo do PR (a) um comando bloqueado pelo `guard-bash.sh` (ex.: tentativa de `pip install`) com a mensagem de bloqueio, e (b) um arquivo `.py` com erro de lint deliberado corrigido após feedback do `check-quality.sh`.
- CI verde no PR.
- **CodeRabbit — regra de parada:** comentários bloqueantes resolvidos; nitpicks respondidos ou registrados como issue, não necessariamente implementados. Sem loop ilimitado.

## Pontos de consulta ao advisor (Fable 5, obrigatórios)

1. Validação dos ADRs 0001–0003 antes de cravar (1.3).
2. Consulta final antes de declarar a sessão concluída, com deliverables salvos antes da chamada.
3. Extraordinária: se qualquer correção do harness exigir mudança de comportamento (não só bugfix), ou se surgir decisão de arquitetura não coberta pelo Apêndice A.

## Critérios de aceite

- [ ] CI verde no PR (lint + types + secrets + audit + pytest com o teste de sanidade).
- [ ] Hooks com evidência objetiva de disparo documentada no PR (ver 1.4).
- [ ] Docs canônicos commitados; `grep -ri kobo` no repo (fora `.git/` e `docs/design/mvp/` — ver Adendo) retorna vazio.
- [ ] `uv.lock` commitado; nenhuma dependência além das listadas em 1.1 (nova dependência = justificativa no PR).
- [ ] Compose sobe SurrealDB pinado; dump local do backup comprovado funcionando; upload OCI parametrizado (não validado — sem credenciais nesta sessão).
- [ ] ADRs 0001–0002 mergeados (0003 desejável, adiável).
- [ ] Template + índice de ADR em `docs/adr/`.
- [ ] `planning/` removido; nada relevante existindo só fora do repo.
- [ ] Checkpoint final com transparência de custo: o que foi delegado a quem, consultas ao advisor e porquês.

## Escopo negativo da sessão

- Nenhum código de produção em `kubo/` além de módulos vazios com docstring. **Única exceção:** `tests/test_sanity.py` (racional em 1.1).
- Nenhuma tabela, migration ou schema (spike e runner = sessão 0002; schema = 0003).
- Nenhum worker, nenhum YAML de catálogo, nenhum código em `runtime/`, `executors/`, `store/` (além de `__init__.py` vazio).
- Gate de cobertura: configurar, não enforçar — e não remover.
- Nenhuma decisão nova de arquitetura sem reabrir planejamento; o Apêndice A é o limite do decidido.
- Nenhum deploy na OCI nesta sessão.

---

## Apêndice A — Registro de decisões do planejamento (confirmadas uma a uma pelo dono, 2026-07-04)

| # | Decisão | Onde se formaliza |
|---|---|---|
| D1 | Nome canônico **Kubo** (docs `kobo-*` renomeiam; conteúdo corrigido) | ADR-0001, nesta sessão |
| D2 | Backup: `surreal export` diário + cópia para OCI Object Storage, desde o M1 | compose nesta sessão; validação no deploy |
| D3 | Tabela mínima **`run`** (worker, início, fim, status, métricas, erro); `produced_by -> run` até a fase 3 | ADR-0002, nesta sessão |
| D4 | Contrato de worker: **runtime persiste** o RunResult (itens tipados/pydantic); `ctx` do worker é somente-leitura escopada por permissões; idempotência por chave natural (hash source+id externo) + upsert; sem fila de retry na fase 1; manifest com `schema_version` inteiro | ADR no M4 |
| D5 | Embeddings **via API/LiteLLM** (não Ollama); modelo e dimensão decididos no spike M2; índice HNSW em migration separada das tabelas; troca de modelo = migration + re-embed | ADR no M2 |
| D6 | Destilação na fase 1 = **worker sob contrato** chamando LiteLLM nas junções via ctx; persona `destilador` formaliza na fase 3. Regras anti-injection: (1) LLM sobre conteúdo coletado nunca tem tools; (2) saída estruturada validada por schema antes de persistir; (3) conteúdo demarcado como untrusted no prompt; (4) conteúdo coletado nunca flui para executor `cli` sem gate humano | ADR no M4/M6 |
| D7 | Migrations: **runner próprio** (~100 linhas; `.surql` numerados + tabela `migration`; sem down-migrations — rollback é nova migration) | esqueleto no M2; ADR no M2 |
| D8 | Auth da API: **bearer token estático** (segredo por referência) + security list OCI | ADR-0003 (nesta sessão, adiável) |
| D9 | Consulta da fase 1: **CLI** (`kubo query`, `kubo show --provenance`) sobre a store; prova dos 90 dias = **teste de aceitação executável** | M6 |
| D10 | **Scribe = stretch goal** (fora do caminho crítico; YouTube via captions primeiro; se necessário, avaliar faster-whisper/API antes de whisper.cpp). **Entidades:** match exato por nome normalizado, sem fuzzy/merge automático. **Loader: só `integrations`** na fase 1 (personas/flow_templates quando tiverem consumidor). Catálogo é sempre diretório 1-arquivo-por-item (promoção fase 4 = PR + merge humano) | ajustes registrados aqui; ADRs nos marcos que os tocarem |

Fontes: sessão de planejamento Cowork de 2026-07-04, com 2 consultas ao advisor (Fable 5): lacunas/sequenciamento e validação deste plano (GO com emendas, todas aplicadas).

---

## Notas de execução (2026-07-04, sessão CLII) — pendências herdadas pelo M2

Registradas aqui (não só em comentário de YAML) para que o M2 herde obrigação explícita. Correções de harness aplicadas nesta sessão além das previstas, todas bugfix (harness comprovadamente funcional), com reconcile do advisor:

1. **Religar o serviço SurrealDB no CI (M2).** O bloco `services:` do GitHub Actions não tem campo de comando e a imagem oficial é distroless (sem shell) — não sobe servidor sem `start ...`, então subiria e sairia, matando o job. Como o M1 não tem teste de integração, o serviço foi **deferido** (não é remoção de gate: sequenciamento). No M2, subir o SurrealDB num *step* `docker run -d surrealdb/surrealdb:<pin> start --user ... --pass ... memory` + probe `surreal isready --conn ...` antes de rodar os testes `@pytest.mark.integration`. Backend `memory` no CI (sem volume, mais rápido).
2. **Enforce do gate de cobertura a partir do M3.** Em `ci.yml`, `--cov-fail-under=85` está comentado (módulos vazios não geram dados). Descomentar no M3, quando `kubo/store` passar a existir por TDD.
3. **Pin definitivo do SurrealDB.** `v2.1.4` (compose + comentário do CI) é provisório; o pin definitivo sai do spike M2 (ADR).
4. **Harness corrigido:** `stop-tests.sh` e `check-quality.sh` usavam `command -v pytest`/`ruff`/`pyright` (global), que num projeto uv retornam falso — os hooks no-opavam silenciosamente. Reescritos para `uv run`. `stop-tests.sh` também: 1 execução (era até 3×), flag inexistente `--deselect-on-failure` removida, exit 5 (nenhum teste) tratado como não-bloqueante. Gate de segredos do CI trocado de `detect-secrets scan --baseline` (sai 0 mesmo com segredo novo — vácuo) para `detect-secrets-hook` (falha, exit 1), comprovado empiricamente.

---

## Adendo (2026-07-04, pós-aprovação) — design MVP no repo

Entre a aprovação deste plano e sua execução, o dono produziu e commitou o design de referência em `docs/design/mvp/` (export do Claude Design), com nota de decisões em `docs/design/README.md`. Impactos NESTA sessão (nenhum reabre o escopo):

1. **Critério do grep ajustado** (acima): `docs/design/mvp/` é export vendorizado e contém "Kobo" em namespace interno de build — excluído da verificação.
2. **Tarefa 1.1 (scaffolder) estendida em um item mecânico:** ao materializar `docs/kubo-design-system.md`, aplicar a emenda de identidade "Direção B" EXATAMENTE como especificada na tabela de `docs/design/README.md` (seção "Identidade visual") — é transcrição, não decisão; a decisão já é do dono (2026-07-04).
3. **Remoção:** `docs/design/mvp/uploads/kobo-design-system.md` (duplicata com nome antigo do doc canônico) deve ser removida no mesmo PR, se o dono ainda não o tiver feito.
4. Terceira consulta ao advisor registrada na sessão de planejamento (skills no DB — D12, ver `docs/design/README.md`); não afeta o M1.
