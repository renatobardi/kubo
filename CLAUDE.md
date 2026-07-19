# CLAUDE.md — Kubo (工房)

**Raiz local do repositório:** `/Users/bardi/Projects/Github/kubo`
Todos os caminhos relativos neste documento resolvem a partir dessa raiz. Remote: `github.com/renatobardi/kubo`.

## O que é este projeto

Kubo é um ateliê pessoal de agentes: coleta informação, cura conhecimento em grafo, distribui resultados e fabrica os próprios workers/agentes. Mantenedor solo (Renato Bardi). **Ambiente de produção: VPC já existente do dono na Oracle Cloud (OCI)**, via Docker Compose — toda decisão de deploy/rede assume OCI (compute instance + VCN existentes), não AWS/GCP.

**Documentos canônicos (leia antes de qualquer trabalho estrutural):**
- `/Users/bardi/Projects/Github/kubo/docs/kubo-spec-funcional.md` — especificação funcional. É a fonte de verdade de escopo e conceitos.
- `/Users/bardi/Projects/Github/kubo/docs/kubo-design-system.md` — UI/UX integral (tokens, componentes, layout). Toda interface segue este documento, sem invenção.

Em conflito entre este arquivo e a spec, a spec vence — e o conflito deve ser apontado ao dono.

## Invariantes (não negociáveis sem decisão explícita do dono)

1. **Um runtime:** Python 3.12+. Não introduzir outras linguagens de aplicação.
2. **Um banco:** SurrealDB (document + vector + graph). Não adicionar segundo datastore. Todo acesso a banco passa pela camada `kubo/store/` — nunca queries espalhadas.
3. **Três catálogos YAML** (`catalogs/integrations/`, `catalogs/personas/`, `catalogs/flow_templates/`): declarativos, 1 arquivo por item, versionados. **Templates são dados, não código** — proibido evoluí-los para DSL (condicionais, herança, hooks). Lógica pertence a skill de persona ou a worker.
4. **Template versionado, instância snapshot:** instanciar flow = congelar cópia da config. Mudança em template nunca afeta flow em andamento.
5. **Gate humano obrigatório** na promoção de código gerado a pipeline operacional. Nunca implementar bypass, nem como flag.
6. **Contrato de worker** (spec §3.3) é obrigatório para todo worker, portado ou gerado. O runtime valida contrato, não confia em quem escreveu.
7. **Escopo negativo da spec (§1.2) é contrato:** sem proxy de credenciais, sem workflow engine/canvas, sem PM standalone, sem orquestrador pesado (Prefect/Dagster/Temporal/Airflow), sem UI rica na fase 1, sem autonomia total. Se uma tarefa parecer exigir um destes itens, PARE e pergunte ao dono em vez de implementar.
8. **Segredos** só por referência (env/secret manager). Nunca valores em YAML, código, log ou commit.

## Stack e convenções

- **Python:** 3.12+, `uv` para dependências, `ruff` (lint+format), `pyright` (types), `pytest`. Type hints obrigatórios em código de `kubo/`.
- **API/serviço:** FastAPI. **Scheduling:** APScheduler. **LLM via API:** LiteLLM (roteamento por persona). **CLIs agênticos:** adapters sob a abstração `executor` (`api|cli`); Claude Code via Claude Agent SDK.
- **SurrealDB:** schemas conforme spec §2.3. DDL versionada em `store/migrations/`. Nomes de tabelas/arestas em inglês, exatamente como na spec (`flow`, `task`, `persona`, `distilled`, `consults`, `produced_by`...).
- **Front (fase 1):** FastAPI + HTMX + Tailwind 4 com os tokens do design system. Views são descartáveis; o grafo é o contrato.
- **Idioma:** conversas, docs, ADRs e planos de sessão em PT-BR; código, identificadores, schema e — a partir da sessão 0002 (D16) — mensagens de commit e PRs em inglês.

## Comandos

Deps por `uv` (grupo `dev` instala o tooling de qualidade). Os gates locais são exatamente os do CI (`.github/workflows/ci.yml`) — falhou, parou.

```bash
uv sync --frozen                 # instala deps do lock (dev incluso); CI usa --frozen
uv lock --check                  # lockfile íntegro (gate 4)

# Gates de qualidade, na ordem do CI:
uv run ruff check .              # lint (E/F/W/I/S-bandit/B/C90, complexidade ≤ 10)
uv run ruff format --check .     # formatação (sem --check para aplicar)
uv run pyright                   # types (strict em store/contracts/runtime, basic no resto)
uv run detect-secrets-hook --baseline .secrets.baseline $(git ls-files)   # segredos (falha em segredo novo)
uv run pip-audit                 # auditoria de deps

# Testes:
uv run pytest -m "not integration"                       # unit (default local, rápido)
uv run pytest tests/store/test_client.py                 # um arquivo
uv run pytest tests/store/test_client.py::test_nome      # um teste
uv run pytest -m integration                             # só integração (exige SurrealDB, ver abaixo)
# gate de cobertura = suíte COMPLETA (unit+integração) com o banco de pé:
uv run pytest --cov=kubo/store --cov=kubo/contracts --cov=kubo/runtime --cov-fail-under=85
```

Cobertura: o gate `--cov-fail-under=85` **vale a partir do M3** e roda no job `integration` do CI sobre a suíte **completa** (unit+integração) — a store só é exercível contra banco real, então medir cobertura só com unit induziria mock-que-testa-mock. Não é gate do job unit. Cobertura é alarme, não meta; nunca remover o gate.

**SurrealDB para integração** (mesmo comando do spike/CI — backend `memory`, efêmero):
```bash
docker run -d --name surreal -p 127.0.0.1:8000:8000 \
  surrealdb/surrealdb:v3.1.5 start --user root --pass root memory
# stack de produção (RocksDB + sidecar de backup): docker compose up -d
```
Conexão vem só de env (invariante 8): `SURREAL_URL` (default `ws://127.0.0.1:8000/rpc`), `SURREAL_USER`/`SURREAL_PASS`/`SURREAL_NS`/`SURREAL_DB` — ver `.env.example`.

**Squad paralelo:** duas sessões rodando `pytest -m integration` batem no mesmo container/namespace e geram flakes fantasma — cada frente usa `SURREAL_NS`/`SURREAL_DB` próprios (padrão do smoke visual isolado). Worktree e troca de branch: item 11 do CLAUDE.md global.

O par **SDK `surrealdb==2.0.0` ↔ server `v3.1.5`** é pinado por evidência (ADR-0005) e sobe junto: bump de um exige revalidar o outro.

**Deploy DEV** (kubo-test, ADR-0011): use SEMPRE o script — nunca dite os passos manuais de rsync/build/up numa sessão.
```bash
./scripts/deploy.sh   # deploy DEV no kubo-test: rsync + build + migrations + up + smoke /healthz (falha se não-ok)
```

## Estrutura do repositório

```
kubo/
├── CLAUDE.md
├── docs/                    # spec funcional, design system, ADRs
│   └── adr/                 # decisões de arquitetura (formato ADR curto)
├── catalogs/
│   ├── integrations/
│   ├── personas/
│   └── flow_templates/
├── kubo/                    # pacote Python
│   ├── api/                 # FastAPI (rotas, views HTMX)
│   ├── store/               # ÚNICA camada de acesso ao SurrealDB + migrations/
│   ├── runtime/             # flows, tasks, boards, gates
│   ├── executors/           # api (LiteLLM) e cli (adapters)
│   ├── workers/             # workers built-in portados (feed; scribe, harvest futuros)
│   ├── scheduler/           # agendamento APScheduler (ADR-0010); `python -m kubo.scheduler`
│   ├── distribution/        # telegram, smtp, relatórios
│   └── contracts/           # Worker protocol, manifests, validação
├── tests/
├── schedules.yaml           # config de agendamento (operação, não catálogo — ADR-0010)
├── docker-compose.yml
└── pyproject.toml
```

## Fluxo de trabalho

- **Branches:** taxonomia `(feat|fix|chore|docs|test|refactor|ci)/slug` a partir de `main` (`slug` em kebab-case, ex.: `ci/0002-git-flow`). PR sempre, mesmo solo — o PR é o registro. Duas camadas de enforce: guard-bash barra localmente; o CI é o gate final (ver ADR-0004). **Merge por squash-only** — o título convencional do PR vira o commit em `main`.
- **Commits e PRs (D16, a partir da sessão 0002):** mensagens de commit e descrições/títulos de PR em **inglês**, convencionais (`feat:`, `fix:`...), corpo explica o porquê. ADRs, docs, planos de sessão e conversas seguem em **PT-BR**. Histórico anterior à sessão 0002 não se reescreve.
- **Toda decisão de arquitetura** que contrarie ou estenda a spec vira ADR em `docs/adr/` ANTES do código.
- **Code review:** CodeRabbit revisa **no PR, nunca em tempo de commit**. Não instalar CodeRabbit CLI como pre-commit hook nem rodá-lo localmente por padrão. Commits devem fluir rápido; o gate de review é o PR. Comentários do CodeRabbit no PR devem ser respondidos ou resolvidos antes do merge — nunca ignorados silenciosamente.

## Agent skills

Config por-repo que o stack de skills do Matt Pocock (`/ask-matt`, `/wayfinder`, `/triage`, `/to-tickets`...) consome. Detalhe operacional nos arquivos; aqui só o ponteiro.

### Issue tracker

Issues e PRDs vivem como GitHub Issues, via `gh` CLI. Issues podem carregar conteúdo à vontade; único guardrail: "cópia congelada nunca é canônica" (**ADR-0026**, substitui ADR-0024). See `docs/agents/issue-tracker.md`.

### Triage labels

Cinco papéis canônicos, label = nome (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` (sob demanda) + `docs/adr/` na raiz. See `docs/agents/domain.md`.

## TDD — Red/Green/Refactor (obrigatório)

Todo código de produção em `kubo/` nasce por TDD. O ciclo é inegociável e deve ficar **visível no histórico**:

1. **RED** — escreva o teste que expressa o comportamento desejado. Rode. **Mostre a falha** (o teste deve falhar pelo motivo certo — asserção, não erro de import).
2. **GREEN** — implemente o mínimo que faz o teste passar. Rode a suite inteira, não só o teste novo.
3. **REFACTOR** — melhore o design com a suite verde como rede. Sem comportamento novo nesta etapa.

Regras:
- É proibido escrever código de produção sem um teste falhando que o exija.
- Em sessões de agente: apresente o output RED antes de implementar. Se eu pedir "implementa X", o primeiro artefato é o teste de X.
- Commits podem seguir o ciclo (`test: caso X (red)` → `feat: implementa X (green)` → `refactor: ...`) ou agrupar red+green num commit — mas o teste SEMPRE entra no mesmo PR que o código que ele exige.
- Estrutura: `tests/` espelha `kubo/`. Unit por padrão; integração (SurrealDB via docker) marcada com `@pytest.mark.integration`; testes de contrato de worker em `tests/contracts/`.
- Cobertura: `pytest --cov` com **fail-under 85%** em `kubo/store/`, `kubo/contracts/` e `kubo/runtime/`. Cobertura não é meta, é alarme — teste comportamento, não linhas.
- LLMs em testes: **sempre mockados/gravados** (respx/vcr). Nenhum teste depende de chamada real a provider.

## Qualidade — gates automáticos

Ordem de execução local e no CI (falhou, parou):
1. `ruff check` + `ruff format --check` — lint inclui regras de segurança (`S`/bandit), bugbear (`B`), complexidade (`C901 ≤ 10`).
2. `pyright` (strict em `kubo/store/`, `kubo/contracts/`, `kubo/runtime/`; basic no restante).
3. `pytest` (unit sempre; integração no CI e sob demanda local).
4. `uv lock --check` — lockfile íntegro; dependências sempre pinadas via lock.

- Funções: uma responsabilidade; docstring de propósito em tudo que é público; sem comentário que repete o código.
- Erros: exceções específicas do domínio em `kubo/errors.py`; proibido `except Exception: pass`; erros de worker retornam estruturados em `RunResult`, não explodem o runtime.
- Logs: `structlog` (JSON), com `flow_id`/`task_id`/`worker` como contexto. Proibido logar payloads com dados sensíveis ou segredos.

## Segurança

- **Segredos:** só por env/secret manager (invariante 8). `detect-secrets` roda no CI (e opcionalmente como hook do harness) — nunca como bloqueio de digitação, sempre como gate de PR/CI.
- **Dependências:** `pip-audit` no CI a cada PR + semanal (cron). Dependência nova exige justificativa no PR (regra do CLAUDE.md) e passa por auditoria.
- **Entrada externa é hostil por padrão:** todo conteúdo coletado (RSS, HTML, transcrição) é dado não-confiável — validado com pydantic nas bordas, sanitizado antes de virar prompt (prompt injection em conteúdo coletado é ameaça de primeira classe neste projeto: agentes leem o que os workers coletam).
- **Código gerado por agente** (fase 4): além do gate humano (invariante 5), roda os mesmos gates de qualidade + validação de contrato antes do PR sequer ser aberto.
- **Superfície de rede (OCI):** serviços do compose não expõem portas além do necessário; SurrealDB nunca exposto fora da VCN — sem regra de ingress na security list/NSG para ele; API com auth mesmo sendo pessoal, exposta apenas pelas portas explicitamente liberadas na security list da OCI.
- **Deploy DEV (fase 1 — oute-server, ADR-0011):** a fase 1 roda num container LXC (`kubo-test`, `10.173.117.18`) no `oute-server` com Docker aninhado, **não** direto na OCI. É **Tailscale-only**: a fronteira de segurança é o bind no IP Tailscale (`100.66.254.24`), **não** o firewall (a faixa DEV 3000-3999 é pública) — logo nada escuta em `0.0.0.0`. Alocação de portas no `PORTS.md` do host (Kubo: 3900 DEV, 2900 PRD reservada). Operação, setup do ambiente (IP estático, DNS do dockerd, AppArmor via `dpkg-divert`) e restore: `docs/runbook-deploy.md`. PRD (OCI) permanece a produção-alvo (direção não-normativa no ADR-0011).
- **Permissões de persona** (YAML) são o mecanismo de least-privilege: worker/persona só acessa as integrações declaradas. O loader valida e o runtime nega o resto.

## Orquestração de modelos (premissa do projeto)

Topologia de três camadas — executor barato embaixo, inteligência máxima só nos momentos de decisão:

| Camada | Modelo | Papel |
|---|---|---|
| **Thread principal (executor/orquestrador)** | **Opus** | Interpreta a missão, decompõe em tarefas, delega aos subagents, valida todo output contra spec/invariantes/gates, conduz checkpoints. Não escreve volume de código na thread. |
| **Advisor (via `/advisor`)** | **Fable 5** | Consultor estratégico auto-invocado: decisões de arquitetura, ADRs, ambiguidade de spec, erros recorrentes, validação de abordagem antes de travar caminho e antes de declarar conclusão. |
| **Subagents (execução)** | Sonnet (código) / Haiku (mecânico) | Trabalho bruto, conforme tabela abaixo. |

**Configuração no início de cada sessão (depende do ambiente):**
- **Claude Code CLI** (onde o comando existe): rodar `/advisor` e selecionar **Fable 5** (ou o mais capaz oferecido no picker). O advisor será auto-invocado pelo modelo principal.
- **Cowork / Claude Code desktop** (sem `/advisor`): o advisor é o subagent **`fable-advisor`** (`.claude/agents/fable-advisor.md`, model Fable 5), invocado **manualmente pela thread principal** nos pontos definidos pela disciplina abaixo. A auto-invocação não existe nesse ambiente, então a disciplina vira responsabilidade ativa da thread — pular consulta obrigatória é violação da política.
- Preferência: **sessões de execução no CLI** (advisor nativo + hooks no habitat natural); Cowork serve bem para sessões de planejamento/entendimento.

**Disciplina de uso do advisor (vale para os dois mecanismos):**
- Consultar **antes de trabalho substancial** — leitura leve primeiro tudo bem, mas o advisor entra antes de escrever/editar/travar abordagem.
- Consultar **pelo menos 2x em tarefas longas**: antes de fixar a abordagem e antes de declarar conclusão.
- **ADR nunca é cravado sem passar pelo advisor.** O draft pode vir do `doc-writer`, a decisão passa pelo Fable, o registro final é da thread principal.
- Se a evidência empírica contradisser o conselho, consultar de novo expondo o conflito — nunca sobrescrever silenciosamente.
- Salvar deliverables antes de chamadas de advisor em pontos de conclusão.

**Roteamento de subagents por natureza da tarefa** (definidos em `.claude/agents/`):
| Natureza da tarefa | Subagent | Modelo |
|---|---|---|
| Scaffolding, boilerplate, configs, docstrings, formatação | `scaffolder` | Haiku |
| Implementação de features, refactors (ciclo TDD, lado GREEN) | `implementer` | Sonnet |
| Escrita de testes a partir de spec de comportamento (lado RED) | `test-writer` | Sonnet |
| Documentação, ADR draft, mensagens de commit/PR | `doc-writer` | Haiku |
| Revisão de segurança de código sensível | `security-reviewer` | Sonnet (achados sobem pra thread principal) |
| Decisões de arquitetura, ADR, ambiguidade de spec | **thread principal + advisor (Fable 5)** | — |

**Regras de qualidade e segurança do roteamento:**
1. **Custo-benefício nunca compra risco:** código que toca `kubo/store/`, `kubo/contracts/`, `kubo/executors/` ou `catalogs/` pode ser *escrito* por Sonnet, mas a validação final linha a linha é da thread principal — e mudanças estruturais nesses caminhos passam pelo advisor. Haiku nunca escreve código de produção nesses caminhos.
2. **Validação é obrigatória e ativa:** output de subagent não é aceito por confiança. A thread principal verifica contra a spec, os invariantes deste documento e o resultado dos gates (ruff/pyright/pytest) antes de integrar. Se o subagent errou, devolve com instrução corrigida — não refaz o trabalho na thread (isso quebra a economia da política).
3. **Escalação explícita:** Haiku falhou 2x → Sonnet; Sonnet falhou 2x ou a tarefa revelou decisão de design escondida → thread principal assume e consulta o advisor; registra no checkpoint por que a tarefa era mais difícil do que parecia.
4. **Contexto mínimo suficiente:** cada delegação leva instrução autocontida (arquivo(s) alvo, contrato esperado, critério de aceite, trecho relevante da spec) — subagent não herda a conversa inteira.
5. **TDD atravessa a delegação:** `test-writer` produz o RED; `implementer` produz o GREEN; a thread principal valida que o ciclo aconteceu de verdade (teste falhava pelo motivo certo antes do código existir).
6. **Transparência de custo:** nos checkpoints, reportar o que foi delegado a quem, quantas consultas ao advisor ocorreram e por quê.

## Modo de trabalho: entender → planejar → autorizar → executar

**Nenhuma sessão sai executando.** Toda sessão nova começa em modo de planejamento (Plan Mode do Claude Code quando disponível):
1. **Entender:** ler os documentos canônicos, o estado do repo e o histórico relevante. Nada de criar/editar arquivo nesta etapa.
2. **Planejar:** apresentar entendimento, apontar lacunas/ambiguidades da spec, propor o plano da sessão com marcos e delegações.
3. **Autorizar:** só após aprovação EXPLÍCITA do dono ("pode executar" ou equivalente) a execução começa — e restrita ao que foi aprovado.
4. **Executar:** com checkpoints nos marcos, conforme o plano aprovado.
Pedido ambíguo do dono não é licença para executar a interpretação mais provável — é gatilho para a etapa 2.

### Divisão de ambientes e o contrato de transferência

- **Cowork (Fable 5 direto):** planejamento, spec, ADRs, decisões de arquitetura. Se a sessão for tocar código além de `docs/` e `catalogs/`, ela pertence ao CLI.
- **Claude Code CLI (Opus + `/advisor` Fable 5):** execução de código, conforme a política de orquestração.
- **Ambos operam na MESMA raiz:** `/Users/bardi/Projects/Github/kubo`. Não existe workspace paralelo do Cowork — todo artefato nasce dentro do repo e vira commit. O repositório Git é a única ponte de contexto entre os ambientes; nada relevante pode existir só na conversa.

**Planos de sessão (`docs/sessions/`):** toda sessão de planejamento no Cowork termina gerando `docs/sessions/NNNN-<tema>.md` commitado, contendo: missão, marcos, delegações previstas (subagent/modelo), pontos de consulta ao advisor, critérios de aceite e escopo negativo da sessão. Esse arquivo é o contrato que a sessão de execução consome.

**Sessões pré-autorizadas por plano:** uma sessão do CLI aberta com referência a um plano commitado (ex.: "execute `docs/sessions/0001-fundacao.md`") nasce com a etapa 3 cumprida — a autorização é o plano aprovado e commitado. Ela ainda DEVE: (a) começar com um "entender" curto (ler o plano + estado atual do repo e confirmar que nada divergiu); (b) executar SOMENTE o que o plano cobre — qualquer coisa fora dele reabre a etapa 2; (c) manter os checkpoints dos marcos. Plano commitado autoriza o escopo do plano, nunca mais que isso.

## Harness (hooks do Claude Code)

O repo carrega um harness determinístico em `.claude/` (estilo hapai: bash puro, zero dependências):
- `PreToolUse` bloqueia: comandos bash destrutivos/perigosos, edição de arquivos de segredo, `git push --force` em main, commit direto em main, criação de branch fora da taxonomia, troca de branch com árvore suja (protege frentes paralelas/worktree — item 11 do CLAUDE.md global), escrita fora do repo.
- `PostToolUse` roda `ruff` + `pyright` no arquivo tocado após cada edição — feedback imediato, não no fim.
- `Stop` roda a suite unit antes de encerrar o turno — turno não termina com teste quebrado.
Os hooks são parte do repo e evoluem por PR como qualquer código. Se um hook bloquear algo legítimo, a correção é no hook (por PR), nunca contorná-lo.

- **Definition of done:** ciclo TDD completo + gates de qualidade verdes + doc/ADR atualizados se comportamento ou arquitetura mudou + PR aberto com review do CodeRabbit endereçado. Telas de UI exigem adicionalmente **paridade com o mockup** (tabela de paridade + screenshots lado a lado; regra em `docs/design/README.md`) e **responsividade mobile** (norma "toda tela nasce responsiva", 2026-07-15, mesmo doc) — tela nova sem variante mobile não está pronta.

## Como trabalhar comigo (o dono)

- Fadiga de complexidade é a razão de este projeto existir. Na dúvida entre duas soluções, escolha a que um mantenedor solo entende em 6 meses. Dependência nova exige justificativa explícita.
- Me questione: se eu pedir algo que viola um invariante ou o escopo negativo, aponte antes de executar.
- Prefira entregas verticais finas (fatia funcionando de ponta a ponta) a camadas horizontais completas.
- Checkpoints: em tarefas longas, pare nos marcos definidos e mostre o estado antes de seguir.
