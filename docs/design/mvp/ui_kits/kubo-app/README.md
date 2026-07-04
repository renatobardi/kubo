# Kubo — UI kit

Interactive recreation of the **Kubo** app — a single-owner personal agent atelier. Composed from the design-system primitives (never re-implemented).

Vocabulary (fixed): **Flow** (not workflow), **Persona** (not agent), **Integração** (not credential), **Worker**, **Run** (execução), **Task**, **Board**, **Gate**, **Destilado**, **Entidade**, **Fonte**. Single owner (Renato Bardi) — no multi-user, signup, or team screens.

## Navigation & screens
Grouped sidebar (the order tells the story: what I know → what's running → what went out → how the shop is built):

- **Home** (ungrouped) — stats, gate alert, últimas execuções, flows ativos.
- **Conhecimento** group:
  - **Conhecimento** (`ConhecimentoScreen.jsx`) — search over destilados; detail with a first-class **provenance chain** (destilado ← item bruto+URL ← fonte ← run) + **Entidades** tab.
  - **Fontes** (`FontesScreen.jsx`) — sources (youtube/rss/site/api) with última coleta, itens acumulados, and collection **health** badge.
- **Trabalho** group:
  - **Flows** (`FlowsScreen.jsx`) — list → **kanban board**; Humano gate tasks highlighted in amber; pipeline failures as cards in `failed`.
  - **Execuções** (`ExecucoesScreen.jsx`) — runs table + filters + structured error.
- **Distribuição** group:
  - **Destinos** (`DistribuicaoScreen.jsx` → `DestinosScreen`) — Artefatos configurados (digest/relatório: nome, query, destinos, agenda cron) + Destinos (pessoa dono/convidada · sistema webhook/arquivo, channel as a chip). No "Canais" block — channel status lives in Integrações.
  - **Envios** (`DistribuicaoScreen.jsx` → `EnviosScreen`) — send history.
- **Catálogos** group (each a nav item → `CatalogosScreen section="…"`):
  - **Integrações** — secret by reference, never shown.
  - **Personas** — monochrome icon identity, executor api|cli, model, skills, perms.
  - **Templates** — mini state-machine diagram with gates marked.

## Cross-cutting
Status always as `Badge` (same variants as buttons); failure/error always **tinted destructive**, never solid red. Emoji as persona identity everywhere. Tool density (`text-sm`), monochrome. Footer = single owner + light/dark toggle (no account dropdown).

## Structure
- `data.js` — domain data (`window.KUBO_DATA`) + `window.KUBO_STATUS` badge-variant helper
- `Shell.jsx` — sidebar + 72px header + breadcrumb
- `*Screen.jsx` — one per section, each registers on `window`
- `index.html` — sign-in gate + router, loads the bundle + all screens

Namespace for compiled components: `window.KoboDesignSystem_6efae6` (internal build identifier).
