# Sessão 0010 — UI: Conhecimento completo + Execuções + fidelidade ao mockup

> **Status:** aprovado pelo dono (2026-07-11, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (**Opus-main** + `/advisor` Fable 5 — marco 7 toca credencial de banco e `client.py`; marco 1 adiciona leituras em store strict; a regra de fidelidade é julgamento contra referência)
> **Timebox:** 8 horas efetivas (stop-loss) — advisor estima 9–11h; **checkpoint obrigatório às ~4h** (fim do M3): estourou → corta M6 inteiro + busca de Execuções
> **Estrutura:** 1 PR — branch `feat/0010-ui-knowledge` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

Fechar o grupo **Conhecimento** da nav (Destilados ✅ + Entidades + Fontes), abrir **Trabalho** com Execuções, e corrigir a dívida de fidelidade da 0009 (Painel e Destilados conforme o mockup). Quitar a dívida de segurança do ADR-0014 (usuário read-only).

## Decisões e contexto

- **D28 (dono):** escopo = Conhecimento + Execuções + fidelidade + polish + read-only user. Catálogos espera Atores/Modelos; Fluxos/Distribuição esperam backend.
- **REGRA PERMANENTE DE FIDELIDADE (nasce nesta sessão, registrada em `docs/design/README.md` + DoD do CLAUDE.md):** toda tela implementada tem critério de aceite = **tabela de paridade** com a tela correspondente do mockup (`docs/design/mvp/ui_kits/kubo-app/`). Cada elemento estrutural (seções, campos por card, ações, estados vazios, navegação) recebe status: *igual* / *desvio declarado + motivo* (backend inexistente | dado inexistente | limitação técnica | sacrifício registrado) / *fora de escopo*. A regra corta nos DOIS sentidos: adicionar o que o mockup não tem também é desvio. Equivalência em nível de token/estrutura — **nunca** pixel/spacing/inline-style do React. Aceite final = screenshot lado a lado no smoke conferindo a tabela. Antídoto a scope creep: a tabela lista o que entra; o que não está nela está FORA.
- Causa raiz da dívida 0009: o plano mandou "usar tokens" sem "seguir a tela X do mockup" — tokens certos com layout empobrecido. Não se repete.

## Emendas do advisor (E1–E7, todas incorporadas)

- **E1:** SEM detalhe de fonte — `FontesScreen.jsx` é lista pura; detalhe seria desvio no sentido inverso. Botão "Adicionar fonte" do mockup fica fora (backend inexistente, declarado).
- **E2:** Entidades SEM sparkline e SEM "relações" — **corte definitivo por dado inexistente**, não sacrifício: `relates_to` não tem produtor na fase 1 (ADR-0008 §V, vazio); `mentions` não tem timestamp e `distilled.created_at` dos 935 legados = data do import (perda consciente ADR-0012) — sparkline mostraria um paredão falso. Entidade = glifo por `kind` + nome + badge de tipo + contagem de menções + destilados que mencionam.
- **E3:** `distilled` NÃO tem `title` — o card do mockup mapeia `item.title` via `derived_from` (fallback: 1ª linha do summary quando NULL) e fonte a 2 hops. `read_distilled` evitou projeção aninhada de propósito (quirk v3.1.5). O M1 **prova por teste de integração** a projeção de 1 nível (`->derived_from->item.title`); se o quirk morder → compor em Python com 1 query batch por página (20 linhas/página, escala pessoal). Não deixar o implementer descobrir no GREEN.
- **E4:** Badge de fonte com **duas verdades**: derivado de `max(item.collected_at)` por fonte — mostra o FATO ("última coleta há Nd"), não julga "saúde". O estado "degradada" do mockup exige sinal de erro por fonte que o schema não tem — desvio declarado; se importar um dia, a resposta é a dívida nomeada "erro por fonte" na fase do harvest, nunca sinal inventado.
- **E5:** Read-only user começa por **probe empírico timeboxado (30min, M7a)** contra container efêmero: (a) `DEFINE USER ... ON DATABASE ... ROLES VIEWER` no v3.1.5; (b) SDK 2.0.0 autenticando user de database (payload signin ganha ns/db — **muda `client.py`, caminho strict, validação linha a linha da thread**); (c) teste fail-closed: `fail_run` com user read-only DEVE errar. Probe falhou → marco degrada pra "probe documentado + ADR de caminho", sessão não morre. **Criação do user NUNCA em migration** (senha em .surql versionado fura o invariante 8): comando one-time no runbook (dono roda, senha via env/getpass), `SURREAL_USER/PASS` do kubo-api apontando pro user novo no compose; fixture de integração cria o user no setup.
- **E6:** Execuções — desvios declarados: `r.flow` do mockup não existe na fase 1 (omitir); `items` vem de `run.stats` com shape por worker (fallback gracioso). `rate_limit_exhausted` apresenta com badge neutro ("quota") — **apresentação, nunca reclassificação** do `status='error'` armazenado.
- **E7:** ordem de ataque abaixo; polish (M6) é o único marco inteiramente sacrificável.

## Marcos (ordem de ataque do advisor — probes derriscam cedo)

| # | Marco |
|---|---|
| M1 | **Store reads, TDD** (strict, validação linha a linha): entidades com contagem de menções (`array::len(<-mentions<-distilled)` projetando SÓ o necessário — nunca materializar records; quirks pinados por teste de integração: ORDER BY na projeção, LIMIT/START literal clampado), detalhe de entidade (distilled que mencionam), fontes com contagem + `max(collected_at)`, runs paginadas com `error.kind`, totais de paginação. **Inclui os probes de projeção 1-nível da E3** |
| M7a | **Probe read-only user (30min timeboxado)** — E5. Resultado decide M7b |
| M2 | **Execuções** (paridade com `ExecucoesScreen.jsx`): lista de runs, badge por `error.kind`, erro estruturado expansível, stats com fallback. Desvios E6 declarados na tabela |
| M3 | **Fontes** (paridade com `FontesScreen.jsx`): lista com kind/última coleta/itens acumulados/badge de recência (E4). Sem detalhe (E1). **← CHECKPOINT ~4h aqui** |
| M4 | **Entidades** (paridade com a tab do `ConhecimentoScreen.jsx`, versão E2): lista tipada + detalhe com destilados que mencionam |
| M5 | **Retrofit de fidelidade** (nunca cortável): Painel conforme `HomeScreen.jsx` — 4 StatTiles com ícone+clique (o 4º, Entidades, agora existe), cards com header + ação "Ver todas", grid 2 colunas; gate alert e "Fluxos ativos" FICAM FORA (backend inexistente, declarado). Cards de Destilados ganham título (E3) + fonte + data |
| M7b | **Wiring do read-only user** (se probe passou): runbook + compose + client.py se necessário (thread valida linha a linha) |
| M8 | **Deploy + smoke browser** (gated no "pode executar"; inclui reboot + screenshots lado a lado conferindo as tabelas de paridade) + emenda ADR-0014 + notas |
| M6 | **Polish (só se sobrar tempo):** view toggle D13b (container-class + listener `htmx:afterSwap` reaplicando estado do localStorage; aproximação declarada — markup único, não os dois markups do mockup), total na paginação, `error.kind` amigável no detalhe do distilled |

## Tabelas de paridade (derivadas dos JSX; o que não está aqui está FORA)

- **Painel** (`HomeScreen.jsx`): PageHeader com descrição ✓ · 4 StatTiles ícone+clique (Fontes→/sources, Itens→/runs, Destilados→/distilled, Entidades→/entities) ✓ · grid 2 col ✓ · card "Últimas execuções" com header/descrição/ação "Ver todas" + ícone por run ✓ · gate alert = **desvio declarado** (backend inexistente) · card "Fluxos ativos" = **desvio declarado** (idem).
- **Destilados lista** (`ConhecimentoScreen.jsx`): busca ✓ (já existe) · card com título do item (E3) + fonte + data + preview do summary ✓ · view toggle = M6/sacrificável · datas dos 935 legados clusterizadas no dia do import = **fato conhecido (ADR-0012), não bug** — nota na sessão.
- **Fontes** (`FontesScreen.jsx`): lista com glifo por kind ✓ · nome/canonical ✓ · última coleta ✓ · itens acumulados ✓ · badge de recência (E4, 2 estados) = **aproximação declarada** do badge de saúde · "Adicionar fonte" = **desvio declarado**.
- **Entidades** (tab `ConhecimentoScreen.jsx`): glifo por kind ✓ · nome + badge tipo ✓ · contagem de menções ✓ · detalhe com destilados que mencionam ✓ · sparkline = **desvio declarado (dado inexistente, E2)** · relações = **desvio declarado (E2)**.
- **Execuções** (`ExecucoesScreen.jsx`): lista com worker/quando/duração ✓ · badge por error.kind ✓ · erro estruturado expansível ✓ · stats com fallback ✓ · busca = sacrificável no checkpoint · coluna flow = **desvio declarado (E6)**.

## Segurança

- Sessão é toda **GET** — o tripwire de CSRF do ADR-0014 NÃO dispara; **afirmado aqui para ninguém "aproveitar" e criar POST** (R4 do advisor).
- Autoescape + proibição de `|safe` seguem valendo em toda tela nova (título de item e nome de entidade são conteúdo coletado/derivado de LLM — hostis).
- Read-only user: fail-closed provado por teste (E5c). Segredos só por env (invariante 8) — criação via runbook, nunca migration.

## Pontos de consulta ao advisor (obrigatórios)

1. Emenda do ADR-0014 (read-only user) antes de cravar.
2. **Extraordinária:** probe E5 falhar de forma não prevista; quirk de projeção (E3) sem workaround limpo; qualquer tentação de POST/mutação.
3. Conclusão da sessão.

## Tarefas do dono

- Rodar o comando one-time de criação do user read-only no servidor quando a sessão pedir (senha nova via env/getpass — rito de sempre) + atualizar o `.env`.
- **"Pode executar"** no deploy (M8).
- Conferir os screenshots lado a lado (mockup × tela) no smoke — você é o juiz da paridade.

## Ordem de sacrifício

1. **1º:** M6 inteiro (view toggle, total, error.kind amigável) — no checkpoint das 4h.
2. **2º:** busca na tela de Execuções (fica paginação).
3. **NUNCA cortáveis:** retrofit de fidelidade (M5), Execuções com `error.kind`, Fontes (lista), Entidades (versão E2), read-only user (ao menos o probe M7a documentado), regra de fidelidade registrada nos docs, deploy com smoke de paridade.

## Critérios de aceite

- [ ] Nav Conhecimento completa (Destilados/Entidades/Fontes) + Execuções — zero link morto.
- [ ] Cada tela nova/retrofitada confere com sua tabela de paridade (screenshots lado a lado no smoke; desvios só os declarados).
- [ ] Painel: 4 StatTiles clicáveis navegando; cards com header/ação.
- [ ] Cards de Destilados com título/fonte/data (fallbacks provados por teste).
- [ ] Execuções discrimina `error.kind` (quota ≠ falha real) com erro expansível.
- [ ] Read-only user: probe registrado; se viável, kubo-api rodando com ele e teste fail-closed verde (escrita NEGADA).
- [ ] Regra de fidelidade registrada em `docs/design/README.md` + DoD do CLAUDE.md (commitados neste PR).
- [ ] Cobertura ≥85% mantida; emenda ADR-0014; PR conforme; main verificado ponta a ponta.
- [ ] Notas de execução com fila da 0011 (view toggle se cortado, Catálogos, dívidas nomeadas).

## Escopo negativo da sessão

- Detalhe de fonte NÃO (E1). Sparkline/relações de entidade NÃO (E2 — dado inexistente). "Adicionar fonte" NÃO. Estado "degradada" NÃO (E4).
- POST/mutação NENHUM (tripwire CSRF não dispara — e não é convite). Reclassificar `status` armazenado NÃO (E6).
- Catálogos NÃO. Fluxos/Distribuição NÃO. Markdown em summary NÃO (E1 da 0009 segue). Pixel-perfection NÃO (paridade é estrutural).
- Sinal de erro por fonte NÃO (dívida nomeada pra fase do harvest, se o dono sentir falta). Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-11; achado do dono (divergência mockup×entregue na 0009); consulta de validação ao advisor (Fable 5): GO com emendas E1–E7, todas incorporadas — regra de fidelidade como tabela de paridade (dois sentidos, estrutural não pixel), cortes por dado inexistente vs sacrifício distinguidos, probes cedo (projeção 1-nível; read-only user com criação via runbook nunca migration), badge de fonte factual, ordem de ataque M1→7a→2→3→4→5→7b→8→6 com checkpoint às 4h, Opus-main mantido (store strict + credencial de banco + julgamento de paridade).*
