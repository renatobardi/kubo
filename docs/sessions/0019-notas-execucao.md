# Sessão 0019 — notas de execução

> Complemento do plano `0019-ui-mobile.md`. Marcos 19.1–19.6 executados; 19.7 (deploy +
> validação física) fica **gated no "pode executar"** do dono, conforme o próprio plano.

## Estado dos marcos

| # | Marco | Estado |
|---|-------|--------|
| 19.1 | Docs/norma mobile | ✅ § Mobile em `kubo-design-system.md`, norma "toda tela nasce responsiva" + v3 supersede `mvp/` em `docs/design/README.md`, linha no DoD do `CLAUDE.md`, `fase4-roadmap.md` renumerado |
| 19.2 | Shell responsivo | ✅ sidebar `hidden md:flex`, bottom tab bar `md:hidden` com safe-area, `h-dvh`, CSS `nav-collapsed` escopado a `≥md`, header mobile large-title com mecanismo opt-in `mobile_back_href`/`mobile_title` |
| 19.3 | Gates no celular | ✅ GateSheet full-screen em mobile (mesmo `<dialog>`+`showModal()`, só a moldura muda por breakpoint); board usa o mecanismo do 19.2 como POC (chevron→`/flows`, título=pergunta real) |
| 19.4 | Varredura por tela | ✅ Destilados/Entidades detail com chevron+título; busca sticky só em Destilados; metadados secundários (`hidden md:inline-flex`) em Fontes/Envios/Destinos/Fluxos/Entidades |
| 19.5 | Tab Mais + pills Saber | ✅ `/more` (19.2) + pills Entidades/Fontes no topo de Destilados (`md:hidden`) |
| 19.6 | Paridade + screenshots | ✅ este documento + smoke visual local (abaixo) |
| 19.7 | Deploy + validação física | ⏸ gated — aguarda "pode executar" do dono |

**Suite:** 500 testes unit verdes (30 novos desta sessão: `test_mobile_shell.py`,
`test_mobile_gates.py`, `test_mobile_sweep.py`), `ruff`/`ruff format`/`pyright` verdes.
Zero teste de layout quebrado — os testes pré-existentes (`test_shell.py`,
`test_dashboard.py`, `test_flows.py`...) continuam verdes sem alteração, o que é em si a
evidência de que nenhuma classe/estrutura desktop foi removida (toda mudança foi aditiva:
`max-md:`, `md:hidden`, `hidden md:flex`).

**Consulta ao advisor (fim do 19.2):** GO com ressalvas — achados endereçados: rebuild
local do Tailwind (binário standalone v4.3.2 pinado, `kubo/api/static/app.css` é
gitignored por design, gerado no build da imagem — não é arquivo stale commitado como o
advisor temeu de início) e `/more` passou a derivar as rotas excluídas de `MOBILE_TABS`
em vez de hardcodar. A sugestão de usar o GateSheet como POC do mecanismo
`mobile_back_href`/`mobile_title` foi seguida no 19.3, o que revelou a necessidade real
de `mobile_title` (não só `mobile_back_href`) — aplicado desde então em todas as telas de
detalhe.

## Smoke visual local (evidência do 19.6)

Ambiente **local, efêmero, não-produtivo**: SurrealDB `memory` já rodando (mesmo comando
do CLAUDE.md) num namespace isolado (`kubo_ui_smoke`, dropado ao final), dados de exemplo
inseridos por script descartável, `uvicorn` local (porta 8901) com `KUBO_PASSWORD_HASH`/
`SESSION_SECRET` gerados só para a sessão. Nada tocou o kubo-test (DEV) nem produção.
Screenshots via `claude-in-chrome` (mobile ~390×844, desktop ~1440×900 lógico).

**Mobile (emulação):** Painel, Destilados (+ pills Saber + busca sticky), detalhe de
Destilado (chevron+título real), Entidades, Fontes, Execuções, Envios, Destinos, Mais,
Fluxos (lista + board horizontal-scroll), GateSheet full-screen (Aprovar/Rejeitar
operável com o dedo, textarea de motivo, botões grandes). Todas as 5 tabs navegáveis;
tab ativa destacada corretamente em cada grupo (`Trabalho` ativo em `/flows/f1`,
`Distribuição` ativo em `/dispatches` etc.).

**Desktop (antes/depois):** Painel, detalhe de Destilado (breadcrumb "Conhecimento ›
Destilados", sidebar, link "‹ Voltar aos destilados" inline, proveniência em coluna),
board de Fluxos (colunas + GateSheet como painel de 440px à direita). Idênticos ao
design pré-0019 — nenhuma classe desktop foi removida, só `hidden`/`md:*` aditivos.
**Ressalva honesta:** não foi feito um diff pixel-a-pixel contra o commit anterior à
sessão (exigiria rodar dois app instâncias paralelas ou um worktree separado); a
evidência de "zero regressão" combina (a) captura visual atual mostrando o desktop
estruturalmente intacto e (b) a suite de testes pré-existente do shell/desktop passando
sem alteração. Se o dono quiser o diff pixel-a-pixel formal, é um passo adicional barato
antes ou durante o 19.7.

## Tabela de paridade — vs. `docs/design/v3/templates/kubo-mobile/KuboMobileApp.jsx`

| Elemento do kit | Kubo mobile | Status |
|---|---|---|
| Bottom tab bar (`TABS`: Painel·Saber·Fluxos·Execuções·Mais) | Painel·Saber·**Trabalho**·**Distribuição**·Mais | **Desvio declarado (plano, marco 19.2):** a tab bar espelha os 5 grupos da nav DESKTOP (Trabalho=Fluxos+Execuções, Distribuição=Destinos+Envios), não o array flat do kit — o kit não modela uma tab "Distribuição" |
| `LargeHeader` (título 30px + subtítulo) | Header mobile do shell (`base.html`), título = `crumb.label` ou `mobile_title` opt-in | Igual — mesma tipografia/peso; subtítulo omitido (o kit usa subtítulo só no Painel, que já tem descrição própria no conteúdo) |
| Navegação em pilha por-tab (`push`/`pop`, `stacks` no state) | Navegação normal de página + botão do browser | **Desvio pré-declarado (C2, advisor):** artefato de SPA, não replicado — chevron-voltar (`mobile_back_href`) substitui o `pop()` |
| `Conhecimento` (tela consolidada com pills internas) | Tab "Saber" → `/distilled` direto, pills Entidades/Fontes no topo | **Desvio pré-declarado (C3):** sem tela nova; pills cobrem o mesmo caso de uso com o dado real já existente em Destilados |
| `GateDetail` (página de detalhe na pilha) | `<dialog>` nativo, full-screen abaixo de `md` (mesmo mecanismo do desktop, só a moldura muda) | Equivalente funcional — full-screen, sem bottom-sheet arrastável (escopo negativo). Smoke cobriu aprovar/rejeitar (report gate) operáveis com o dedo; a variante "confirmar promoção" **herda a mesma moldura** (é o mesmo `<dialog>`, mudança só de conteúdo interno) mas não foi smoked diretamente — validação real fica pro 19.7 |
| `Card` (full-width, `p-16`) | Cards existentes do desktop (`rounded-xl`/`rounded-2xl`, `ring-1 ring-foreground/10`) já são full-width em mobile por herança do layout de lista | Igual em efeito; token de raio segue o valor do desktop (não o do kit) — divergência de token pré-existente, fora do escopo desta sessão |
| `SearchField` (sticky, usada em várias telas) | Sticky só em Destilados | **Sacrifício de timebox pré-declarado (plano, item 2):** as demais listas mantêm busca não-sticky |
| `Mais` (links + toggle de tema + conta) | `/more`: lista simples de links (Entidades/Fontes/Execuções/Envios) | **Desvio pré-declarado (C3):** riqueza do kit (tema/conta) não replicada — toggle de tema já vive na topbar desktop, ausente do header mobile (gap conhecido, não bloqueante) |
| `MobileSparkline` (menções ao longo do tempo) | Placeholder "sem dados ainda" (já um desvio herdado do desktop, D-a) | Fora de escopo desta sessão — dado não existe até o EPIC-A (relações/timestamp) |
| Telas sem componente equivalente no kit (Destinos, Envios) | Gramática mobile aplicada por extensão (large-title, cards, metadados secundários ocultos) | Sem 1:1 no kit — o kit não modela essas telas; paridade avaliada contra a gramática geral, não um componente específico |

## Escopo negativo confirmado (nada disto foi implementado)

Sem SPA/hx-boost, sem swipe, sem bottom-sheet arrastável, sem PWA, sem tela Conhecimento
consolidada, sem telas novas fora do que o plano listou, sem Playwright, sem breakpoint
tablet — conforme `0019-ui-mobile.md`.

## Próximo passo

19.7 (deploy no kubo-test + validação física do dono no aparelho real via tailnet,
Safari real) — gated, aguardando autorização explícita antes de tocar o ambiente DEV.
