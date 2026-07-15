# Sessão 0018 — Rito de promoção: aprovar vira pipeline

> **Status:** aprovado pelo dono (2026-07-15, planejamento no Cowork); advisor GO com emendas incorporadas
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas — advisor estima 6-7h para a parte A. **Corte pré-acordado após o marco 18.6** (rito provado com PR de teste do dono); o restante é a **sessão 0018b, PRÉ-AUTORIZADA por este mesmo plano** (marcos 18.7–18.11), sem replanejamento
> **Estrutura:** 1 PR (ou 2, com o corte) — branch `feat/0018-promotion-rite` (D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.
> **Contexto:** fase 4, sessão 2 do roadmap (`docs/sessions/fase4-roadmap.md`); spec §3.4; metodologia AI-DLC (ADR-0020)

---

## Missão

O momento de maior risco do sistema (spec §3.4) vira mecânica provada: worker sob contrato chega por PR ao repo do Kubo → gate na UI → **merge do dono** → deploy → botão **"Confirmar promoção"** (valida via API do GitHub + import no processo vivo) → worker oficialmente promovido no grafo. Na 0018b, o **agente** produz o worker de estreia (`github-releases`) — o ciclo da fase 4 completo. Instanciação de pipeline agendado = sessão 0019.

## Decisões do dono

- **D41:** workers gerados vivem em `kubo/workers/` no repo principal; agente abre PR contra `renatobardi/kubo`. Cruza conscientemente o escopo negativo da 0016, com proteções por construção (abaixo).
- **D42/D43:** worker de estreia = **`github-releases`** (coleta releases de repos GitHub configurados).
- **D44:** o flow dev (agente) produz o worker no smoke da 0018b. Fallback pré-declarado: agente tropeçou 2x → CLI assume a escrita; o rito já estará provado pela parte A, nada em risco.
- **D45:** conta-máquina **`kubo-dev`** no GitHub (collaborator do repo) — o PAT do agente é dela. Gate humano vira enforçado pelo GitHub (dono revisa PRs que não são dele; CODEOWNERS + require review).

## Emendas do advisor (GO condicionado — todas incorporadas)

### Segurança do agente no repo principal (0018b)
- **E1 — Conta-máquina é pré-requisito** (D45): PAT fine-grained da `kubo-dev`, restrito ao repo kubo, contents+PR write. PR de agente identificado por autor == `kubo-dev` (nunca heurística de branch).
- **E2 — PAT SEM a permissão Workflows** (a proteção estrutural mais importante): o GitHub **rejeita** push que toque `.github/workflows/` — o CI não pode ser neutralizado pelo próprio PR. Sem isso, path-guard é teatro.
- **E3 — Path-guard como job de CI + required status check:** se `github.actor == 'kubo-dev'`, o diff deve estar contido em `kubo/workers/**`, `tests/workers/**`, `catalogs/**`. Lógica **inline no workflow** (nunca script do repo que o PR poderia editar). Fora do allowlist → merge bloqueado por branch protection.
- **E4 — Higiene pré-primeiro-clone:** rodar gitleaks/detect-secrets no **histórico completo** do repo uma vez (baseline cobre working tree, não histórico). Confirmado: `.env` gitignored, só `.env.example` no repo.
- **E5 — Loop de auto-amplificação NOMEADO no ADR-0021:** código de agente mesclado vira contexto dos próximos agentes. Review humano/CodeRabbit trata **comentários, docstrings e strings** de PR de agente como superfície de ataque. A qualidade do review do dono é componente de segurança, não só de qualidade.
- **E6 — `.claude/` no workspace do agente:** o clone traz hooks/settings que o Claude Code do subprocess carrega. Decisão na sessão: worker limpa `.claude/` do workspace OU o ADR aceita e nomeia (conteúdo do dono; questão de previsibilidade).
- **E7 — CI de PR same-repo roda com secrets do repo:** auditar que `ci.yml` não tem secret além do `GITHUB_TOKEN`; fixar `permissions: contents: read` no workflow.
- **E8 — Whitelist de repo-alvo do flow dev** (forge + kubo; nunca arbitrário) — entra na 0018b junto com o PAT.

### Mecânica do rito (parte A)
- **E9 — Registro = código + relocação do registry:** ANTES do agente existir no circuito, refactor (CLI/Sonnet): `WORKER_REGISTRY` sai de `kubo/scheduler/__init__.py` para **`kubo/workers/registry.py`** (só o dict; scheduler importa de lá). PR de worker vira: `kubo/workers/x.py` + 1 linha no registry + testes — contido no path-guard. Continua dict hardcoded (ADR-0010 intacto; emenda de localização no ADR-0021). Bônus: API importa o registry sem puxar APScheduler. **NÃO criar `catalogs/workers/`** (seria 4ª categoria — invariante 3; registro = código + manifest no código).
- **E10 — Import no processo vivo É o oráculo do deploy** (sem BUILD_ID, sem subprocess): Confirmar promoção = (1) GitHub API `merged: true` + gravar `merge_commit_sha` no grafo; (2) `worker_name in WORKER_REGISTRY` + manifest válido **no processo da API**. Se o registry resolve, a imagem contém o merge por construção. Deploy não rodou → erro estruturado "worker não está na imagem; rode ./scripts/deploy.sh" → gate segue aberto, dono reclica. Edge nomeado no ADR (não resolvido agora): ancestralidade merge antigo vs PR novo — `merge_commit_sha` gravado basta para auditoria com 1 operador.
- **E11 — Sem flow novo: dev-mini v2** — a spec (§3.1, linha do gate de promoção) manda o rito ser *gate declarado no template dev* (`done → promoted`). `done` deixa de ser terminal neste template; flows antigos protegidos pelo snapshot (invariante 4). Custo real da sessão: generalizar a maquinaria do ADR-0019 §XII para **gate sequencial** (aprovar→done cria a PRÓXIMA task humana; terminal-ness derivada do snapshot — sem transição de saída = terminal — nunca de literais). Botão Confirmar = `decide_gate` no par `(done, promoted)` com validações de E10 ANTES da decisão (I/O externo antes do commit, ordem do reject). **Terceira porta de escrita da UI = emenda ao D38/ADR-0018.** Se a generalização do gate sequencial custar >~3h → consulta extraordinária (alternativa: flow separado, com emenda à spec, nunca silencioso).
- **E12 — Leitura do merge NUNCA com o PAT de escrita:** o confirmar só lê; usa o token read-only de E13. Não repetir no repo principal o blast radius aceito pro sandbox.
- **E13 — Integração nova `github-releases.yaml`** com `secret_ref: env:GITHUB_TOKEN_READONLY` (PAT read-only): o `github.yaml` atual carrega credencial de ESCRITA — coletor com ela violaria least-privilege. Este é o YAML legítimo do "mesmo PR". Rate limit com token: 5000 req/h.
- **E14 — Promoção é ambiente-local por construção** (o processo valida a si mesmo) — propriedade, não acidente; nomeada no ADR. Card parado em `done` com gate aberto deve ser legível no board ("aguardando deploy + confirmação"), senão em 6 meses parece bug.

### Spec da task do worker github-releases (0018b — cravar TUDO, senão o agente inventa)
- Contrato ADR-0009; manifest declara `integrations: [github-releases]`.
- Config: lista de repos (config de execução, espelho do feed — NÃO catálogo).
- Dedupe: `release.id` como chave natural + upsert (releases são editadas; idempotência §VII). Só `published`; sem drafts; prereleases fora no v1.
- Release notes = markdown de terceiros = **hostil** (disciplina de coleta existente; primeiro conteúdo de terceiros pelo caminho novo — nada dele flui pro executor cli, gatilho 0023b intacto).
- Rate limit 403/429 → `ErrorInfo` estruturado, **sem retry** (retry = orquestrador; o dono é o scheduler).
- Suite unit-only no workspace do agente (sem SurrealDB lá).

## Marcos (ordem de ataque)

### Parte A — o rito (corte aqui)
| # | Marco |
|---|---|
| 18.1 | **ADR-0021 esqueleto** (rito de promoção: deploy-gap como desvio consciente da §3.4, import-oráculo, registro=código, relocação do registry, 3ª porta de escrita, loop de auto-amplificação E5, promoção ambiente-local E14) |
| 18.2 | **Refactor registry** → `kubo/workers/registry.py` (Sonnet + validação linha a linha; toca kubo/scheduler/) |
| 18.3 | **dev-mini v2 + gate sequencial** (E11): `promoted` no board, gate `[done, promoted]`, maquinaria §XII generalizada (próxima task humana criada no done; terminal-ness do snapshot) |
| 18.4 | **Confirmar promoção** (E10/E12): handler + validações (merged+SHA via API read-only; registry+manifest em processo) + UI (GateSheet do gate de promoção, estado "aguardando deploy") |
| 18.5 | **Higiene E4** (scan de histórico) + auditoria E7 (ci.yml permissions/secrets) |
| 18.6 | **Deploy + smoke parte A (gated no "pode executar"):** worker de teste trivial escrito pelo CLI num PR normal → gate → merge do dono → deploy → Confirmar promoção → promovido no grafo. Testar TAMBÉM o caminho "confirmar sem deploy" (erro estruturado legível). **⟵ PONTO DE CORTE** |

### Parte B — o agente no repo principal (0018b)
| # | Marco |
|---|---|
| 18.7 | **Preparos do dono (runbook literal):** conta-máquina kubo-dev + convite collaborator + PAT dela (contents+PR write, SEM Workflows) + PAT read-only (E13) + CODEOWNERS + require review + envs no .env |
| 18.8 | **Path-guard no CI** (E3, required check) + whitelist de repo-alvo no flow dev (E8) + decisão E6 (.claude/ no workspace) |
| 18.9 | **Integração `github-releases.yaml`** (E13) + spec da task do worker (seção acima) |
| 18.10 | **Smoke 0018b (gated):** dono cria a task → agente implementa o github-releases → PR do kubo-dev (código + registry + YAML + testes) → CI/path-guard/CodeRabbit → gate na UI → merge do dono → deploy → Confirmar promoção → **primeiro worker de agente promovido**. Fallback D44 se tropeçar 2x |
| 18.11 | **ADR-0021 final (advisor valida antes de cravar)** + notas de execução + memória |

## Pontos de consulta ao advisor (obrigatórios)

1. Antes de fixar a abordagem do gate sequencial (18.3) — é o desconhecido que come horas.
2. ADR-0021 antes de cravar.
3. Antes de declarar conclusão (0018 e 0018b).
4. Extraordinária: generalização do gate sequencial >~3h (alternativa: flow separado com emenda à spec).

## Critérios de aceite

- Parte A: promoção provada fisicamente — merge sem deploy dá erro legível; com deploy, Confirmar registra `merge_commit_sha` no grafo e o flow transiciona `done → promoted`. Suite completa verde, cobertura ≥85% nos módulos core.
- Parte B: PR real do `kubo-dev` passando path-guard + CI + CodeRabbit + gate + merge do dono; worker `github_releases` importável no registry; promovido de ponta a ponta. Path-guard testado no sentido negativo (PR do kubo-dev tocando fora do allowlist → bloqueado).
- Custo do agente dentro do budget; `total_cost_usd` registrado.

## Escopo negativo

- SEM instanciação de pipeline/agendamento (0019); SEM poll/webhook de merge; SEM carga dinâmica de código — jamais; SEM capacidade de merge no Kubo (D38); SEM `catalogs/workers/` (4ª categoria = decisão de invariante 3 que NÃO está sendo tomada); SEM retry no coletor; sem template dev-aidlc ainda (dev-mini v2).

## Sacrifícios pré-declarados (ordem)

1. Smoke do caminho negativo do path-guard adiado (mecânica idêntica, teste unit cobre).
2. Worker github-releases degradado: CLI escreve (fallback D44) — o rito não depende do autor.
3. Se o gate sequencial travar: parte A termina com promoção via flow dev-mini v2 SEM generalização plena (gate duplo hardcoded no behavior dev-mini, generalização vira dívida nomeada) — só com aval do advisor.
