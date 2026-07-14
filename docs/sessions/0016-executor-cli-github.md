# Sessão 0016 — Executor `cli` + GitHub: a persona dev nasce

> **Status:** aprovado pelo dono (2026-07-14, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5) — sessão de fronteira (executor novo + credenciais novas + subprocess)
> **Timebox:** 8 horas efetivas — advisor estima que NÃO fecha a fatia inteira: **o corte VAI disparar**. Ponto de corte PRÉ-ACORDADO após o marco 16.5 (mecanismo cli provado no container, repo coerente); o restante é a **sessão 0016b, PRÉ-AUTORIZADA por este mesmo plano** (marcos 16.6–16.9), sem replanejamento
> **Estrutura:** 1 PR (ou 2, com o corte) — branch `feat/0016-executor-cli-github` (D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

Fechar a fase 3 da spec (§5): o dono cria uma task dev → persona **dev** (executor `cli`, Claude Code via **Claude Agent SDK**, Sonnet) implementa num clone efêmero do repo sandbox → worker faz push e abre **PR real no GitHub** → gate humano na UI (resumo + link do PR, mecânica ADR-0018) → **aprovar** grava a decisão e fecha o flow (o merge é clique do dono no GitHub); **rejeitar** fecha o PR via API com o motivo em comentário. Sem autonomia de criação: dono cria tasks, agentes executam.

## Decisões do dono

- **D35:** fatia vertical completa — task → PR real → gate. É o esqueleto que a fase 4 reusa.
- **D36:** modelo **Sonnet** na persona dev + **budget por flow implementado NESTA sessão** (`budget_usd` no template; runtime corta quando o custo real reportado pelo SDK estoura). `ANTHROPIC_API_KEY` paga, via env (invariante 8).
- **D37:** repo **sandbox fixo privado**, criado manualmente pelo dono (ex.: `renatobardi/kubo-forge`); **PAT fine-grained restrito a esse único repo** (`contents:write`, `pull_requests:write`). Side-effect "criar repo na instanciação" (spec §3.1) fica pra quando um flow real precisar.
- **D38:** aprovar no gate = decisão no grafo + flow fechado — **Kubo não tem capacidade de merge** (anti-bypass por construção, padrão ADR-0018 §V-bis); o merge é do dono no GitHub. Rejeitar = fechar o PR via API com o `reason` em comentário.

## Correções obrigatórias do advisor (GO condicionado a C1–C4)

### C1 — Disparo do flow dev é por CLI (terminal do dono), NUNCA pela UI
Agente Claude Code roda **minutos**; disparo síncrono no request da UI dispararia o gatilho (b) do ADR-0018 ("executor cli de minutos → o desenho síncrono morre, vira processo executor, ADR próprio + dono na mesa"). O **gate continua na UI** (já existe). Assimetria com o `analysis` (que tem botão) é consciente e registrada no ADR-0019 — até existir "processo executor" com ADR próprio.

### C2 — Clone SEM credencial no workspace
Clonar com `https://x-access-token:PAT@github.com/...` grava o PAT em `.git/config` — legível pelo agente com `cat`. Regra: o remote do workspace é **sempre URL sem credencial**; o worker injeta o PAT **só no momento do push**, fora do alcance do agente (credential helper efêmero ou header por comando), e **nunca loga a URL autenticada** (mesma disciplina da redação do token do bot, ADR-0015).

### C3 — Dois cliques manuais do dono no sandbox (runbook, pré-smoke)
1. **Desabilitar GitHub Actions** no repo sandbox — agente que escreva `.github/workflows/x.yml` num branch pushed executaria código com o `GITHUB_TOKEN` do repo: canal real de exfil/mischief.
2. **Branch protection em `main`** — PAT com `contents:write` consegue push direto em main; a proteção torna "worker só abre PR" verdade **por construção**, não por disciplina.

### C4 — Spike do SDK dentro do container PRIMEIRO (~2h iniciais)
O risco real não é o mecanismo — é **Node.js na imagem + LXC aninhado + env**. Marco 16.2 é um spike de fumaça: o Agent SDK spawna o Claude Code dentro da imagem do Kubo no kubo-test e completa um turno trivial. Se quebrar, sabemos na hora 2, não na 7. Se o LXC aninhado for hostil (AppArmor/spawn) → **consulta extraordinária ao advisor**: alternativa nomeada = container-irmão via Docker aninhado (contrato "prompt in → stream out" sobrevive; muda onde o subprocess vive).

## Emendas do advisor (incorporadas)

- **E1 — Env do agente por WHITELIST, nunca herança:** o subprocess herda o env do pai por default — e o pai carrega SURREAL passwords, `kubo_rw`, GEMINI, `TELEGRAM_BOT_TOKEN`, `GITHUB_PAT_FORGE`. Usar o parâmetro `env` do `ClaudeAgentOptions` passando **somente** `ANTHROPIC_API_KEY` + `PATH` + `HOME` apontando pro workspace; `cwd` pinado no workspace. `disallowed_tools`: cortar `WebFetch`/`WebSearch` (barateamento de superfície a custo zero).
- **E2 — Budget = teto-com-overshoot, no `CliExecutor`, nunca no runner:** o SDK reporta usage por mensagem e `total_cost_usd` no `ResultMessage`, mas o custo chega DEPOIS do gasto do turno — enforcement corta logo após estourar (overshoot ≤ 1 turno, nomeado no ADR). Backstops mecânicos no executor: `max_turns` + timeout de wall-clock. Estouro → `ErrorInfo(kind="budget")` estruturado no `RunResult`. Runner permanece camada fina (ADR-0016 §III — budget no runner = cheiro de segundo mecanismo, parar na hora). **No template: um escalar `budget_usd`** — enumera FATO, runtime decide o que fazer (lista negativa ADR-0016 §I intacta; congela no snapshot, invariante 4). NÃO aceitar: budget por estado, fallback de modelo, retry-on-budget. Isto **quita o gatilho (c) do ADR-0016** — citar no ADR-0019. Modelo (`sonnet`) mora na persona, não no template. **Se o SDK pinado não expuser custo utilizável no stream → budget degrada para `max_turns`+timeout e `budget_usd` NÃO entra no template** (campo mentiroso = ADR-0016 §VIII).
- **E3 — Estrutura NUNCA vem do texto do agente** (espelho de "citações nunca via LLM", ADR-0016 §VI): URL do PR, nome do branch e todo dado estrutural vêm das **respostas da API do GitHub**; o agente contribui só prosa. Deliverable `kind=pr` guarda a URL que a API devolveu. **Com teste.**
- **E4 — Relatório do agente no GateSheet = untrusted no consumo:** saída de LLM que leu um filesystem inteiro. Texto plano, `pre-wrap`, nunca markdown→HTML — regime exato do `deliverable.content` (ADR-0018 §VI), sem exceção.
- **E5 — "Nada a pushar" = falha estruturada:** agente termina sem diff (ou só lixo) → task `failed`, sem PR vazio — caminho com teste. Branches órfãos (flow falhou pós-push): nome de branch derivado do flow id (único por construção); limpeza = nota de runbook, não código.
- **E6 — Limites honestos NOMEADOS no ADR-0019, com gatilhos:**
  - Agente roda no MESMO container/UID do Kubo — `/proc/<pid>/environ` do pai entrega segredos a quem tiver RCE. Contenção real hoje = **conteúdo que o agente lê é do dono** (sandbox privado, task do dono, sem PRs de terceiros). **Gatilho: antes de a fase 4 misturar conteúdo coletado/de terceiros no circuito do executor cli, o agente migra para container-irmão isolado.**
  - `permission_mode` headless (bypass de permissões no subprocess): a contenção é workspace+env+conteúdo-do-dono, não prompts. O invariante 5 NÃO está arranhado — o gate humano está no PR, não no turno do agente.
  - Deps do projeto sandbox são código que executa (`uv sync` roda postinstall de terceiros): sandbox minimalista, stdlib ou deps pinadas pelo dono.
  - Node.js na imagem = runtime vendorizado de ferramenta terceira (como um binário), **não** linguagem de aplicação — invariante 1 intacto, frase registrada para não reabrir em 6 meses. Pin `claude-agent-sdk` + versão do CLI com evidência (disciplina ADR-0005).
- **E7 — Identidade de commit:** worker configura `user.name`/`user.email` no workspace (trava o run se faltar). Workspace efêmero: `rm -rf` pós-run em `finally` (inclusive falha), senão o disco do LXC enche de clones.
- **E8 — Simetria anti-bypass no catálogo:** `github.yaml` declara SÓ push / open-PR / close-PR-com-comentário — **merge ausente por capacidade** (D38). O `reason` do reject (input do dono) vai pro comentário sem interpolação esquisita.

## O template `dev-mini.yaml` (forma proposta — validar contra a lista negativa na execução)

```yaml
name: dev-mini
version: 1
board:
  states: [created, implementing, review, done, rejected, failed]
  transitions: [[created, implementing], [implementing, review], [implementing, failed], [review, done], [review, rejected]]
gates: [[review, done], [review, rejected]]
cast: [dev, humano]
deliverable: pr
triggers: [manual]
budget_usd: 5.0
```

Persona `dev.yaml`: `executor: cli`, modelo Sonnet, prompt de engenheiro disciplinado (implementa SÓ o que a task pede, roda testes, commita), `permissions: [github]`. FLOW_REGISTRY ganha o behavior `dev-mini` (código + PR = gate humano, ADR-0016 §IV).

## Marcos (ordem de ataque — a peça assustadora primeiro)

| # | Marco |
|---|---|
| 16.1 | **ADR-0019 esqueleto** (C1–C4, E1–E8, D35–D38, quitação do gatilho (c) do ADR-0016, assimetria de disparo registrada) |
| 16.2 | **SPIKE (C4): Agent SDK dentro da imagem no kubo-test** — Node na imagem, spawn do CLI, env whitelist, um turno trivial completo. Quebrou → consulta extraordinária (container-irmão) |
| 16.3 | **`CliExecutor` (TDD, SDK mockado):** contrato "prompt in → stream de eventos out", workspace efêmero, env whitelist (E1), budget+`max_turns`+timeout (E2), eventos → structlog |
| 16.4 | **Integração `github.yaml` + operações** (push com credencial injetada C2, open-PR, close-PR-com-comentário; E3 estrutura-da-API com teste; redação de URL autenticada) |
| 16.5 | **Worker dev sob contrato ADR-0009** (clone → agente → diff-check E5 → push → PR), persona `dev.yaml` + template `dev-mini.yaml` + FLOW_REGISTRY. **⟵ PONTO DE CORTE PRÉ-ACORDADO** |
| 16.6 | *(0016b)* **Runtime/gate:** deliverable `kind=pr`, GateSheet com resumo untrusted (E4) + link do PR, `kubo flow run dev-mini` (C1) |
| 16.7 | *(0016b)* **Preparos do dono (runbook §2d-style):** sandbox repo + PAT fine-grained + Actions OFF + branch protection (C3) + `ANTHROPIC_API_KEY` no .env — passo a passo literal, valores nunca no chat |
| 16.8 | *(0016b)* **Deploy `./scripts/deploy.sh` + smoke físico (gated no "pode executar"):** task real → PR real → caminho APROVAR (dono mergeia no GitHub) E caminho REJEITAR (PR fechado com motivo via API) — screenshots de paridade |
| 16.9 | *(0016b)* **ADR-0019 final (advisor valida antes de cravar)** + notas de execução + reconciliação de custo real do smoke |

## Pontos de consulta ao advisor (obrigatórios)

1. Resultado do spike 16.2 (mesmo se passar — confirmar que o desenho segue).
2. ADR-0019 antes de cravar.
3. Antes de declarar conclusão (0016 e 0016b).
4. Extraordinária: LXC hostil ao spawn (C4) ou SDK sem custo utilizável (E2).

## Critérios de aceite

- Spike 16.2: um turno do Claude Code completo DENTRO da imagem no kubo-test, com env whitelist provado (agente não enxerga SURREAL/TELEGRAM/PAT — teste que lê o env do subprocess).
- `CliExecutor` sob TDD com SDK mockado; budget corta com `ErrorInfo(kind="budget")`; timeout e `max_turns` funcionam.
- PR real aberto no sandbox com URL vinda da API (E3, com teste); PAT jamais em `.git/config`, log ou erro (C2, com teste).
- Gate dos dois caminhos provado fisicamente no browser (0016b): aprovar → flow `done`, merge manual do dono; rejeitar → PR fechado com motivo, flow `rejected`, NADA mergeado.
- Suíte completa verde, cobertura ≥85% em store/contracts/runtime, gates de qualidade todos.

## Escopo negativo da sessão

- **Nunca tocar o repo do Kubo** — agente trabalha SÓ no sandbox (fase 4 é outra sessão, com rito completo).
- Sem merge via API (D38); sem side-effect criar-repo (D37); sem gemini/goose; sem autonomia de criação de tasks; sem botão de disparo na UI (C1); sem fila/processo executor; sem container-irmão nesta sessão (gatilho registrado); sandbox privado, sem PRs/conteúdo de terceiros.
- Budget: sem fallback de modelo, sem retry-on-budget, sem budget por estado (E2).

## Sacrifícios pré-declarados (ordem)

1. Caminho rejeitar do smoke adiado (aprovar prova o ciclo; rejeitar entra dias depois — a mecânica é a da 0015, já provada).
2. `close-PR-com-comentário` degradado para fechar sem comentário (motivo fica só no grafo).
3. Se o spike 16.2 quebrar sem saída nas 8h: sessão vira "spike + ADR do aprendizado" e o advisor redesenha o onde-vive-o-subprocess — sem improvisar arquitetura no talo da sessão.
