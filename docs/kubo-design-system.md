# Kubo Design System

> Extraído integralmente do Valmis (`apps/web`) — shadcn-svelte estilo **maia**, Tailwind CSS 4, tokens OKLCH.
> Portável para qualquer stack de frontend: os tokens são CSS variables puras.

---

## 0. Identidade canônica — Direção B v2 (decisão do dono, 2026-07-04; revisada 2026-07-06)

> **Esta seção prevalece.** O restante do documento é a extração crua do Valmis
> (**Direção A**, rejeitada); mantido abaixo apenas como referência histórica.
> Onde a Direção B diverge, vale a Direção B. A revisão v2 (export do dono em
> `docs/design/mvp/`) tornou a Direção B definitiva: **não há mais opção de
> revert** para elementos da Direção A.

| Item | Direção A (Valmis, rejeitada) | **Direção B v2 (canônica)** |
|---|---|---|
| Primária | Âmbar queimado `oklch(0.555 0.163 49)` | **Preto mono** `oklch(0.216 0.006 56)` (dark inverte p/ quase-branco `0.92`) |
| Títulos | Noto Serif (`--font-heading`) | **Inter em tudo**, self-hosted (`InterVariable.woff2`, sem Google Fonts). Noto Serif **eliminada na v2** — sem opção de revert |
| Identidade de persona | Emoji de lista preset | **Glifos Lucide monocromáticos** em círculo muted (`PersonaGlyph`). Emoji **removido por completo na v2** (`agent-emojis` deletado do export) — nunca emoji, em nenhum contexto |
| Logo | — | **Sakura de linha** (5 pétalas, traço mono; componente `Sakura`, variantes `mark`/`full`/`glyph`). Light: pétalas `--sakura-petal #f4c9d4` + traço near-black; dark: só contorno rosa, sem preenchimento. **Única cor fora da paleta stone, escopada ao logo** — não vaza para UI. *v2 substitui o kanji 智 da v1* |
| Tagline | — | "The art of getting things done" (raiz: 段取り *dandori*) |

**Permanece da identidade original (inegociável nas duas direções):** stone quente,
botões-pílula com afundamento de 1px, cards planos com `ring-1 ring-foreground/10`
(sem shadow/border), destructive sempre *tinted*, charts monocromáticos, densidade
`text-sm`, bordas translúcidas no dark.

**Idioma (regra do dono, 2026-07-06):** PT-BR é **só apresentação** — labels de
navegação e textos visíveis na UI. Código, variáveis, rotas, identificadores,
nomes de componente e schema: **inglês mandatório** (coerente com D16/CLAUDE.md).
Ex.: label "Destilados" → rota `/distilled`, tabela `distilled`.

Notas v2: a inconsistência interna do export v1 ("emoji everywhere" vs. "nunca
emoji") foi **resolvida** — "nunca emoji" vale em todos os documentos. Faxina
pendente no export: `fonts.css` ainda importa Noto Sans JP do Google (resíduo do
logo kanji v1) — remover, kanji não existe mais na identidade.

---

## 1. Fundações

### Stack de origem
| Camada | Escolha |
|---|---|
| Framework CSS | Tailwind CSS 4 (`@theme inline`, sem tailwind.config) |
| Base de componentes | shadcn-svelte, style **maia**, baseColor **stone** |
| Primitivos headless | bits-ui |
| Variantes | tailwind-variants (`tv()`) |
| Ícones | Lucide (default) + Iconify (integrações) |
| Animações | tw-animate-css |
| Fontes | Fontsource Variable (self-hosted) |

### Tipografia
```css
--font-sans: 'Inter Variable', sans-serif;      /* corpo, UI */
--font-heading: 'Noto Serif Variable', serif;   /* títulos */
```
**A assinatura visual do Valmis é essa dupla:** UI inteira em Inter, mas todo título (page header, card title, dialog title, logo) em **Noto Serif** com `font-medium`/`font-semibold` + `tracking-tight`. É o contraste serifado que dá o ar editorial.

Escala observada em uso:
- Logo/brand: `font-heading text-2xl font-semibold tracking-tight`
- Título de página (h1): `font-heading text-xl font-semibold tracking-tight`
- Título de card/dialog/sheet/popover: `font-heading text-base font-medium`
- Corpo padrão: `text-sm` (o app inteiro roda em 14px)
- Descrições/meta: `text-sm text-muted-foreground` ou `text-xs`

### Raios (assinatura forte)
```css
--radius: 0.5rem;
--radius-sm: calc(var(--radius) - 4px);   /* 4px  */
--radius-md: calc(var(--radius) - 2px);   /* 6px  */
--radius-lg: var(--radius);               /* 8px  */
--radius-xl: calc(var(--radius) + 4px);   /* 12px */
--radius-2xl: calc(var(--radius) * 1.8);  /* 14.4px */
--radius-3xl: calc(var(--radius) * 2.2);  /* 17.6px */
--radius-4xl: calc(var(--radius) * 2.6);  /* 20.8px */
```
**Botões usam `rounded-4xl` (pill).** Cards usam `rounded-2xl`. Essa combinação — botão-pílula + card bem arredondado — é o que faz o Valmis parecer "soft" sem ser infantil.

---

## 2. Cores (OKLCH)

Paleta neutra **stone** (quente) com primária **âmbar/bronze queimado** (hue ~46–58).

### Tema claro (`:root`)
```css
--radius: 0.5rem;
--background: oklch(1 0 0);
--foreground: oklch(0.147 0.004 49.25);
--card: oklch(1 0 0);
--card-foreground: oklch(0.147 0.004 49.25);
--popover: oklch(1 0 0);
--popover-foreground: oklch(0.147 0.004 49.25);
--primary: oklch(0.555 0.163 48.998);            /* âmbar queimado */
--primary-foreground: oklch(0.987 0.022 95.277); /* creme */
--secondary: oklch(0.967 0.001 286.375);
--secondary-foreground: oklch(0.21 0.006 285.885);
--muted: oklch(0.97 0.001 106.424);
--muted-foreground: oklch(0.553 0.013 58.071);
--accent: oklch(0.97 0.001 106.424);
--accent-foreground: oklch(0.216 0.006 56.043);
--destructive: oklch(0.577 0.245 27.325);
--border: oklch(0.923 0.003 48.717);
--input: oklch(0.923 0.003 48.717);
--ring: oklch(0.709 0.01 56.259);
--chart-1: oklch(0.869 0.005 56.366);
--chart-2: oklch(0.553 0.013 58.071);
--chart-3: oklch(0.444 0.011 73.639);
--chart-4: oklch(0.374 0.01 67.558);
--chart-5: oklch(0.268 0.007 34.298);
--sidebar: oklch(0.985 0.001 106.423);
--sidebar-foreground: oklch(0.147 0.004 49.25);
--sidebar-primary: oklch(0.666 0.179 58.318);
--sidebar-primary-foreground: oklch(0.987 0.022 95.277);
--sidebar-accent: oklch(0.97 0.001 106.424);
--sidebar-accent-foreground: oklch(0.216 0.006 56.043);
--sidebar-border: oklch(0.923 0.003 48.717);
--sidebar-ring: oklch(0.709 0.01 56.259);
```

### Tema escuro (`.dark`)
```css
--background: oklch(0.147 0.004 49.25);          /* stone-950 quente */
--foreground: oklch(0.985 0.001 106.423);
--card: oklch(0.216 0.006 56.043);
--card-foreground: oklch(0.985 0.001 106.423);
--popover: oklch(0.216 0.006 56.043);
--popover-foreground: oklch(0.985 0.001 106.423);
--primary: oklch(0.473 0.137 46.201);            /* âmbar mais profundo */
--primary-foreground: oklch(0.987 0.022 95.277);
--secondary: oklch(0.274 0.006 286.033);
--secondary-foreground: oklch(0.985 0 0);
--muted: oklch(0.268 0.007 34.298);
--muted-foreground: oklch(0.709 0.01 56.259);
--accent: oklch(0.268 0.007 34.298);
--accent-foreground: oklch(0.985 0.001 106.423);
--destructive: oklch(0.704 0.191 22.216);
--border: oklch(1 0 0 / 10%);                    /* branco translúcido! */
--input: oklch(1 0 0 / 15%);
--ring: oklch(0.553 0.013 58.071);
--chart-1..5: (iguais ao claro);
--sidebar: oklch(0.216 0.006 56.043);
--sidebar-foreground: oklch(0.985 0.001 106.423);
--sidebar-primary: oklch(0.769 0.188 70.08);     /* âmbar vivo no dark */
--sidebar-primary-foreground: oklch(0.279 0.077 45.635);
--sidebar-accent: oklch(0.268 0.007 34.298);
--sidebar-accent-foreground: oklch(0.985 0.001 106.423);
--sidebar-border: oklch(1 0 0 / 10%);
--sidebar-ring: oklch(0.553 0.013 58.071);
```

Decisões dignas de nota:
- **Bordas no dark são branco translúcido** (`oklch(1 0 0 / 10%)`), não cinza sólido — se adaptam a qualquer superfície.
- **Charts em escala monocromática stone** (não multicolor) — dashboards ficam sóbrios, a cor primária fica reservada pra ação.
- Dark mode via classe: `@custom-variant dark (&:is(.dark *));`

### Mapeamento Tailwind 4 (`@theme inline`)
Cada variável é exposta como cor utilitária (`bg-background`, `text-muted-foreground`, `border-border`...):
```css
@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-chart-1: var(--chart-1);  /* ...até chart-5 */
  --color-sidebar: var(--sidebar);  /* + todos os sidebar-* */
}
```

### Base layer
```css
@layer base {
  * { @apply border-border outline-ring/50; }
  body { @apply bg-background text-foreground; }
  html { @apply font-sans; }
  button { cursor: pointer; }
}
```

---

## 3. Componentes — receitas de estilo

### Button (a peça mais característica)
Base: **pill** (`rounded-4xl`), `text-sm font-medium`, `h-9` default, focus ring de 3px, e um microdetalhe tátil: `active:translate-y-px` (afunda 1px ao clicar).

```
base: rounded-4xl border border-transparent bg-clip-padding text-sm font-medium
      focus-visible:ring-[3px] focus-visible:border-ring focus-visible:ring-ring/50
      active:not-aria-[haspopup]:translate-y-px
      inline-flex shrink-0 items-center justify-center whitespace-nowrap
      transition-all outline-none select-none
      disabled:pointer-events-none disabled:opacity-50
      [&_svg:not([class*='size-'])]:size-4

variants:
  default:     bg-primary text-primary-foreground hover:bg-primary/80
  outline:     border-border bg-input/30 hover:bg-input/50 hover:text-foreground
  secondary:   bg-secondary text-secondary-foreground hover:bg-secondary/80
  ghost:       hover:bg-muted hover:text-foreground dark:hover:bg-muted/50
  destructive: bg-destructive/10 text-destructive hover:bg-destructive/20
               dark:bg-destructive/20 dark:hover:bg-destructive/30   ← tinted, não sólido!
  link:        text-primary underline-offset-4 hover:underline

sizes:
  default: h-9 px-3 gap-1.5    sm: h-8 px-3    xs: h-6 px-2.5 text-xs    lg: h-10 px-4
  icon: size-9    icon-sm: size-8    icon-xs: size-6    icon-lg: size-10
  (padding assimétrico quando há ícone: has-data-[icon=inline-start]:pl-2.5 etc.)
```
**Destructive é "tinted"** (fundo vermelho a 10–20% + texto vermelho), não botão vermelho sólido — muito mais elegante em telas com várias ações perigosas.

### Card
```
ring-foreground/10 bg-card text-card-foreground rounded-2xl
gap-6 py-6 text-sm ring-1 overflow-hidden flex flex-col
data-[size=sm]:gap-4 data-[size=sm]:py-4
has-[>img:first-child]:pt-0
*:[img:first-child]:rounded-t-xl *:[img:last-child]:rounded-b-xl
```
**Sem shadow, sem border** — o card se define por `ring-1 ring-foreground/10`. Superfície plana, contorno sutil que funciona nos dois temas.

### Badge
Mesmas variantes semânticas do botão (default/secondary/destructive tinted/outline/ghost/link) — vocabulário unificado de cor entre ação e status.

### Sidebar
```
SIDEBAR_WIDTH        = 16rem  (256px)
SIDEBAR_WIDTH_MOBILE = 18rem
SIDEBAR_WIDTH_ICON   = 3rem   (colapsada em ícones)
```
Tokens próprios (`--sidebar-*`) — no dark ela usa a cor de `card` (um degrau acima do background), no claro um off-white. `--sidebar-primary` é mais vivo que o `--primary` do conteúdo.

### Inventário completo de UI (shadcn)
badge, breadcrumb, button, card, command (⌘K palette), dialog, dropdown-menu, input, input-group, label, popover, scroll-area, select, separator, sheet, sidebar, skeleton, switch, table, textarea, tooltip.

### Componentes custom (padrões de produto — mapeiam direto pro Kubo)
- **chat/**: ChatInput, ChatMessage, ChatThreadSidebar, ChatUsageBar, AgentAvatar, AgentCard, FilePreviewSidebar, BrowserSessionDialog
- **home/**: home-stats, home-agents-grid, home-recent-activity, home-top-workflows (dashboard de 4 blocos)
- **workflow/canvas/**: WorkflowBuilder sobre `@xyflow/svelte` + layout automático `@dagrejs/dagre`
- **knowledge/**, **credentials/**, **auth/AuthShell**, alert system, skill-install-dialog
- **agent-emojis.ts**: identidade de agente = emoji de uma lista de presets (🤖🧠💡🔧📊🎯🚀...) — barato e humano; perfeito para personas do Kubo

---

## 4. Padrões de layout

### App shell
```
Sidebar.Provider
 ├─ AppSidebar (16rem, colapsável a 3rem)
 └─ Inset
     ├─ header  h-[72px] border-b border-border/50 px-4
     │    [trigger] [divisor: h-4 w-px bg-border/60] [breadcrumb]
     └─ main    flex-1 flex-col gap-6 p-6
```
Header de 72px casa com o header da sidebar (p-2 + h-14 + p-2). Bordas internas sempre suavizadas (`border-border/50`, `bg-border/60`).

### Page header (todas as páginas)
```html
<h1 class="font-heading text-xl font-semibold tracking-tight">{title}</h1>
<p class="mt-1 text-sm text-muted-foreground">{description}</p>
<!-- ações à direita (shrink-0) -->
<Separator class="mt-4" />
```

### Mapa de navegação (referência pro Kubo)
`/app` → home (stats) · agents · chat · workflows · credentials · knowledge · llm-providers · skills · account

### Mobile (`<md`)

Referência autoritativa: `docs/design/v3/` — **supersede `docs/design/mvp/`** para toda implementação nova (D50, sessão 0019; norma em `docs/design/README.md`). Gramática de app mobile: `docs/design/v3/templates/kubo-mobile/KuboMobileApp.jsx`.

Breakpoint **binário** `<md` (mobile) / `≥md` (desktop) — sem breakpoint intermediário. Desktop nunca muda: mobile é sempre aditivo (`max-md:` para override, `md:hidden` / `hidden md:flex` para trocar sidebar↔tab bar).

- **Bottom tab bar** fixa (`md:hidden`), safe-area (`env(safe-area-inset-bottom)`), substitui a sidebar (`hidden md:flex`): **Painel · Saber · Trabalho · Distribuição · Mais**.
- **Navegação em pilha por-tab do JSX é artefato de SPA — não replicar.** Voltar = navegação normal de página (botão do browser + chevron-voltar no header mobile). Sem `hx-boost` na v1.
- **Header large-title**: 30px/700/tracking `-0.03em` no topo de cada tab; página de detalhe = chevron-voltar + título compacto.
- **`h-dvh`, nunca `h-screen`**, em contexto mobile — Safari iOS quebra 100vh com a toolbar dinâmica (validar no aparelho real, não só emulação).
- **Viewport**: `viewport-fit=cover` na meta tag + `env(safe-area-inset-bottom)` no padding da tab bar.
- **Gate mobile** = página de detalhe full-screen comum — nunca bottom-sheet arrastável.
- **Busca sticky**: só em Destilados na v1 (sacrifício de timebox pré-declarado).
- Tab "Saber" aponta para `/distilled` com pills para Entidades/Fontes no topo — não replica a tela consolidada "Conhecimento" do kit (desvio pré-declarado, ver tabela de paridade da sessão 0019).
- Tab "Mais" é página simples de links para o resto da navegação — não replica a riqueza da tela "Mais" do kit.

---

## 5. Síntese da identidade (o "porquê" do visual)

1. **Serif nos títulos, sans no corpo** — Noto Serif + Inter. É 80% da personalidade.
2. **Neutros stone quentes** + primária âmbar/bronze — nada de azul-SaaS.
3. **Botões-pílula** (`rounded-4xl`) com afundamento de 1px no clique.
4. **Cards planos com ring** em vez de shadow/border.
5. **Destructive sempre tinted**, nunca sólido.
6. **Dark mode com bordas translúcidas** e âmbar mais vivo na sidebar.
7. **Charts monocromáticos** — cor é reservada para ação.
8. **Tudo em `text-sm`** — densidade de ferramenta profissional.
9. **Emoji como avatar de agente** — identidade sem asset pipeline.

## 6. Como portar pro Kubo

- **Copiar seções 2 e do CSS base literalmente** — é CSS puro, framework-agnostic.
- Fontes: `@fontsource-variable/inter` + `@fontsource-variable/noto-serif` (npm) ou Google Fonts (Inter, Noto Serif).
- Se a view do Kubo for **FastAPI + HTMX/Jinja**: Tailwind 4 standalone CLI + este arquivo de tokens + replicar as receitas da seção 3 como macros/partials.
- Se for **React**: `npx shadcn init` com baseColor stone, substituir o CSS gerado por estes tokens, ajustar button/card conforme seção 3 (o estilo "maia" existe no registry do shadcn-svelte; no React reproduz-se com as receitas acima).
- Kanban do Kubo: colunas como superfícies `bg-muted/50 rounded-2xl`, cards como Card `data-[size=sm]`, estados via Badge variants — tudo já coberto pelo vocabulário existente.
