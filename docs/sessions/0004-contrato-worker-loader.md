# Sessão 0004 — Contrato de worker + Loader de integrations (M4)

> **Status:** aprovado pelo dono (2026-07-05, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas (stop-loss) — ordem de sacrifício abaixo
> **Estrutura:** 1 PR — branch `feat/0004-worker-contract-loader` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

Materializar a **fronteira de segurança do projeto**: o contrato de worker (D4 + D6) em `kubo/contracts/`, o runner mínimo em `kubo/runtime/` que o honra, e o loader do catálogo `integrations` — fatia vertical fechando com um **worker fake executado ponta a ponta**: validado contra o contrato, executado com ctx escopado, RunResult persistido pelo runtime via store, `run` registrado. O M5 troca o fake pelo feed real.

## Contexto

M1–M3 ✅. Store completa (39 testes, 99% cobertura, gate 85% no job integration). Carry-overs do 0003 herdados: a forma de `run.stats`/`run.error` se crava AQUI (campos são `FLEXIBLE` no schema — **não exige migration**; modelos pydantic serializam para dentro deles); a aresta `item→run` fica para o M5 (re-registrar nas notas). Decisões vigentes: D4, D6, loader só de `integrations`, catálogo = diretório 1-YAML-por-item, invariante 8.

**Fallback de advisor (registrado):** se o subagent `fable-advisor` reportar o reviewer indisponível e cair no próprio julgamento (ocorreu no 0003), a consulta degradada é aceitável para prosseguir, MAS fica anotada nas notas de execução e o ADR ganha revalidação na consulta de conclusão.

## Marco 4.1 — Contrato (`kubo/contracts/`, TDD, pyright strict)

| # | Tarefa | Quem |
|---|---|---|
| 4.1.1 | **ADR-0009 — contrato de worker** (UM documento; formaliza D4 e absorve D6): `Worker` Protocol (`manifest` + `run(ctx) → RunResult`); manifest pydantic (`name`, `version`, `schema_version: 1`, integrações usadas, **schema de config como CLASSE pydantic** — `type[BaseModel]`, não JSON-schema dict; export para JSON-schema é problema da fase 4); **runtime persiste, worker devolve dados tipados**; ctx somente-leitura escopado; idempotência via chaves naturais da store; sem fila de retry; **forma de `ErrorInfo` e `Stats`** (alinha `run.error`/`run.stats`); **as 4 regras anti-injection (D6) como obrigações do contrato** + seção explícita "o que este ADR não decide" (mecânica de prompt/demarcação/no-tools fica para o ADR do M6 — emenda se precisar, padrão do ADR-0006). Registrar também: logger de worker NUNCA loga payload coletado. Draft `doc-writer` cedo → **advisor valida ANTES do código do contrato congelar** → thread crava | `doc-writer` → advisor → thread |
| 4.1.2 | **Payloads do RunResult — princípio cravado (C1):** união discriminada (`Field(discriminator=...)`) de payloads tipados cujos campos **espelham 1:1 as assinaturas de escrita da store**. Fatia M4 implementa SÓ `SourcePayload` + `ItemPayload` (o que o feed do M5 consome); payload de distilled é M6. `ItemPayload` embute a chave natural da source inline (runner faz upsert da source antes do item; idempotência torna repetição gratuita). SEM generics (`RunResult[T]` proibido — união discriminada basta) | `test-writer` RED → `implementer` GREEN |
| 4.1.3 | **Validação de contrato é função explícita** `validate_worker(obj)` em `kubo/contracts/` — pydantic-valida manifest e checa assinatura de `run`. NÃO confiar em `@runtime_checkable`/`isinstance` (só checa presença de membros — falsa validação). O Protocol serve ao pyright; a função serve ao runtime. Erros de domínio em `kubo/errors.py`. Testes em `tests/contracts/` | idem |

## Marco 4.2 — Runner mínimo (`kubo/runtime/`)

| # | Tarefa | Quem |
|---|---|---|
| 4.2.1 | Fluxo: `validate_worker` → monta `RunContext` → `run()` → valida RunResult → **persiste cada payload via método da store correspondente por match EXPLÍCITO e hardcoded (tipo → função da store; registry/plugin de persistência PROIBIDO — é DSL disfarçada)** → registra ciclo de `run`. **Persistência é por-item idempotente, NÃO transação-mega (C2):** cada upsert da store já é atômico; falha parcial vira erro estruturado no `run`, itens já gravados permanecem, re-execução idempotente cura (por isso D4 dispensou retry). O wrapper transacional NÃO envolve o run inteiro | `implementer`; thread valida linha a linha |
| 4.2.2 | **RunContext (decidido — não deliberar):** `config` (instância validada do schema do manifest), `integrations` (declaradas ∩ catálogo, segredo resolvido pelo runtime), `knowledge` (**seam read-only VAZIO — C3:** Protocol sem métodos; o feed não precisa de leitura do grafo — idempotência elimina até o "já coletei?"; métodos entram quando um worker exigir com teste; expor `db.query` "somente leitura" é PROIBIDO — não há como escopar com segurança; worker nunca recebe handle de db), `logger` (structlog bound com `run_id`/`worker`). **Slot de LLM: NÃO criar campo morto** — "prever o slot" = frase no ADR-0009 dizendo onde entra (M6); adicionar campo depois é trivial | idem |
| 4.2.3 | Exceção de worker capturada NA FRONTEIRA (deliberado): vira erro estruturado no `run`, runtime não explode. Ruff pode reclamar (BLE/S110) — comentário de intenção + noqa cirúrgico; NUNCA afrouxar a regra global | idem |
| 4.2.4 | **Carry-over nomeado (C4):** `finish_run` ganha param `stats` NESTA sessão (o worker fake com métricas é o consumidor que as notas do 0003 pediam) — teste primeiro; sem migration (campo FLEXIBLE) | `test-writer` → `implementer` |
| 4.2.5 | Worker FAKE vive em `tests/` (fixture de `tests/contracts/`/`tests/runtime/`) — `kubo/workers/` é só para os portados reais (C6). Testado nos caminhos sucesso E falha, ponta a ponta com persistência real (integração) | `test-writer` |

## Marco 4.3 — Loader de integrations

| # | Tarefa | Quem |
|---|---|---|
| 4.3.1 | `catalogs/integrations/` (1 YAML por arquivo), schema pydantic: auth **só por referência** a env/secret — loader REJEITA valor inline (teste explícito); rate limits e endpoints declarativos (sem lógica — invariante 3). Primeiro YAML: `rss.yaml` (público, sem auth) | `test-writer` → `implementer` |
| 4.3.2 | **Resolução de segredo — princípio (C5):** o RUNTIME resolve a referência (`env:FOO`) na montagem do ctx; worker nunca lê `os.environ`. Valor resolvido vive só no objeto ctx, nunca em log. Esse é o ponto de enforcement do least-privilege | idem |
| 4.3.3 | **Negação acontece na montagem do ctx, não na validação do manifest** — manifest válido ≠ permissão concedida (separa contrato de autorização). Runtime só injeta integrações declaradas ∩ existentes; resto negado (testado) | idem |

## Pontos de consulta ao advisor (obrigatórios)

1. **ADR-0009 antes do código do contrato congelar** (4.1.1) — é A fronteira de segurança.
2. Conclusão da sessão (deliverables salvos antes) — inclui revalidação do ADR-0009 se a consulta 1 rodou degradada.
3. Extraordinária: contrato colidir com formas da store do M3, ou pyright strict brigar com `type[BaseModel]`/união discriminada (consultar em vez de afrouxar o strict).

## Ordem de sacrifício (timebox 8h)

1. **1º corte:** `rss.yaml` (vira primeiro item do M5).
2. **2º corte:** enforcement de least-privilege reduzido a validação de existência (deny completo desce ao M5).
3. **NUNCA cortáveis:** ADR-0009 validado; contrato + runner com worker fake ponta a ponta (sucesso e falha); loader rejeitando segredo inline.

## Critérios de aceite

- [ ] Worker fake executado ponta a ponta com persistência real (teste de integração), caminhos sucesso e falha.
- [ ] Manifest inválido e RunResult inválido rejeitados via `validate_worker`/validação de resultado, com erros de `kubo/errors.py`.
- [ ] `finish_run(stats=...)` implementado com teste (carry-over 0003 quitado).
- [ ] Loader valida `rss.yaml` e rejeita segredo inline (teste explícito).
- [ ] Negação de integração não-declarada/não-existente na montagem do ctx (testado).
- [ ] Cobertura ≥85% agora exercitando `contracts/` e `runtime/` de verdade.
- [ ] ADR-0009 mergeado (com seção D6 e "o que este ADR não decide").
- [ ] PR conforme (branch/título/template; CodeRabbit endereçado; squash).
- [ ] Notas de execução no plano: pendências para M5 explícitas (aresta `item→run`; deny completo se cortado; `rss.yaml` se cortado).

## Escopo negativo da sessão

- Nenhum worker real (nem feed). APScheduler NÃO (M5). LLM client no ctx NÃO (M6 — sem campo morto). Executors cli NÃO (fase 3).
- Payload de distilled NÃO (M6). Leitura do grafo no ctx NÃO (seam vazio). Registry/plugin de persistência NÃO.
- Loaders de personas/flow_templates NÃO. Deploy NÃO (M5.5). Store sem método novo além do `stats` de `finish_run`.
- Nenhuma decisão nova de arquitetura sem reabrir planejamento.

## Notas de execução (CLI, 2026-07-05)

- **Consulta de advisor #1 (ADR-0009) rodou DEGRADADA:** o `advisor` nativo do CLI está indisponível; o fallback `fable-advisor` reportou reviewer externo indisponível e caiu no próprio julgamento (cenário previsto, linha 19). Veredito: **GO com 7 correções**, todas incorporadas ao ADR-0009 antes de congelar (extra="forbid" nos modelos do contrato; validação numérica de Stats; truncamento de ErrorInfo.message na fronteira; rename WorkerContext→RunContext p/ spec §3.3; semântica payloads+error; last-write-wins + contrato valida forma-não-intenção + escrita não-escopada como limite v1; validate_worker retorna manifest validado, runner não relê obj.manifest — TOCTOU). **ADR-0009 exige revalidação na consulta de conclusão** (linha 19 + ponto de consulta #2).
- **Pendências nomeadas para o M5:** (a) aresta `item→run` (carry-over do 0003, não implementada aqui); (b) deny completo de least-privilege se cortado pelo timebox; (c) `rss.yaml` se cortado.

---

*Fontes: sessão de planejamento Cowork de 2026-07-05; consulta de validação ao advisor (Fable 5): GO com 6 correções, todas incorporadas — payloads espelham a store (C1), persistência por-item sem transação-mega (C2), seam de leitura vazio no ctx (C3), `finish_run.stats` como tarefa nomeada (C4), resolução de segredo pelo runtime (C5), fake em tests/ (C6) — mais: `validate_worker` explícito, config como classe pydantic, sem generics, noqa cirúrgico na fronteira, fallback de advisor degradado registrado.*
