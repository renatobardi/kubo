# Kubo — UI kit

Interactive recreation of the **Kubo** app — a single-owner personal agent atelier. Composed from the design-system primitives (never re-implemented).

Vocabulary (fixed): **Flow** (not workflow), **Persona** (not agent), **Integração** (not credential), **Worker**, **Run** (execução), **Task**, **Board**, **Gate**, **Destilado**, **Entidade**, **Fonte**. Single owner (Renato Bardi) — no multi-user, signup, or team screens.

## Navigation & screens
Grouped sidebar (the order tells the story: what I know → what's running → what went out → how the shop is built):

- **Painel** (ungrouped) — stats, gate alert, últimas execuções, fluxos ativos.
- **Conhecimento** group:
  - **Destilados** (`ConhecimentoScreen.jsx`) — busca sobre destilados; detalhe com **cadeia de proveniência** (destilado ← item bruto+URL ← fonte ← run).
  - **Entidades** (`ConhecimentoScreen.jsx`) — entidades tipadas com sparkline de menções e relações.
  - **Fontes** (`FontesScreen.jsx`) — fontes (youtube/rss/site/api) com última coleta, itens acumulados e badge de **saúde** da coleta.
- **Trabalho** group:
  - **Fluxos** (`FlowsScreen.jsx`) — lista → **board kanban**; gates do Humano em âmbar; falhas de pipeline como cards em `failed`.
  - **Execuções** (`ExecucoesScreen.jsx`) — lista de runs com busca e erro estruturado expansível.
- **Distribuição** group:
  - **Destinos** (`DistribuicaoScreen.jsx` → `DestinosScreen`) — Artefatos configurados (digest/relatório) + Destinos (pessoa dono/convidada · sistema webhook/arquivo). Status de canal vive em Integrações.
  - **Envios** (`DistribuicaoScreen.jsx` → `EnviosScreen`) — histórico de envios.
- **Catálogos** group (cada um → `CatalogosScreen section="…"`):
  - **Integrações** — secret por referência, nunca exposto.
  - **Atores** — identidade em ícone monocromático, executor api|cli, modelo, skills, perms.
  - **Modelos** — mini diagrama de máquina de estados com gates marcados.

Rótulos de navegação em PT-BR (Painel / Fluxos / Atores / Modelos); os termos de domínio (Flow, Persona, Template) seguem no vocabulário conceitual. Toda tela de lista tem um **toggle de views** (Lista / Duas colunas / Quadrados) via `window.ViewToggle` — views que não se aplicam ficam desabilitadas.

## Cross-cutting
Status always as `Badge` (same variants as buttons); failure/error always **tinted destructive**, never solid red. Persona identity = monochrome Lucide glyphs (`PersonaGlyph`), never emoji. Tool density (`text-sm`), monochrome. Footer = single owner + light/dark toggle (no account dropdown).

## Structure
- `data.js` — domain data (`window.KUBO_DATA`) + `window.KUBO_STATUS` badge-variant helper
- `Shell.jsx` — sidebar + 72px header + breadcrumb
- `*Screen.jsx` — one per section, each registers on `window`
- `index.html` — sign-in gate + router, loads the bundle + all screens

Namespace for compiled components: `window.KoboDesignSystem_6efae6` (internal build identifier).
