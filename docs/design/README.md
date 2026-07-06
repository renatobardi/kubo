# Design MVP — nota de decisões

> **Data:** 2026-07-04 · **Revisão v2:** 2026-07-06 · **Status:** ratificado pelo dono
> **Conteúdo:** `mvp/` é o export do Claude Design — design system (tokens, componentes, guidelines) + app navegável (`mvp/ui_kits/kubo-app/`) + screenshots de QA (`mvp/screenshots/`).

## O que este artefato é (e não é)

- **É referência de implementação** para as views do Kubo (fases 2+): arquitetura de informação, hierarquia, componentes, estados, tokens.
- **Não é código de produção.** O export é React/JSX; o Kubo implementa em FastAPI + HTMX + Jinja + Tailwind 4 usando **os mesmos tokens** (`mvp/tokens/*.css` são CSS puro, portáveis). Nenhuma dependência entra por esta porta.
- **Não altera o roadmap:** UI segue fora do caminho crítico da fase 1 (M1–M6). Boards/kanban são fase 3; este material espera lá.

## Identidade visual — Direção B v2 (decisão do dono, 2026-07-04; revisada 2026-07-06)

O mockup diverge deliberadamente do design system extraído do Valmis, e o dono ratificou a divergência. **A Direção B é a identidade canônica do Kubo.** A revisão v2 (novo export do dono, 2026-07-06) a tornou definitiva — sem opção de revert para a Direção A. A tabela canônica vive em `docs/kubo-design-system.md` §0; resumo:

| Item | Direção A (Valmis, rejeitada) | **Direção B v2 (canônica)** |
|---|---|---|
| Primária | Âmbar queimado `oklch(0.555 0.163 49)` | **Preto mono** `oklch(0.216 0.006 56)` (dark inverte p/ quase-branco `0.92`) |
| Títulos | Noto Serif (`--font-heading`) | **Inter em tudo**, self-hosted (`InterVariable.woff2`). Noto Serif **eliminada na v2** |
| Identidade de persona | Emoji de lista preset | **Glifos Lucide monocromáticos** (`PersonaGlyph`). Emoji **removido por completo na v2** (`agent-emojis.js` e cards de emoji/kanji deletados) |
| Logo | — | **Sakura de linha** (componente `Sakura`, 5 pétalas, variantes `mark`/`full`/`glyph`; tokens `--sakura-petal #f4c9d4` / `--sakura-ink`, theme-aware — única cor fora da stone, escopada ao logo). *Substitui o kanji 智 da v1* |
| Tagline | — | "The art of getting things done" (raiz: 段取り *dandori*) |

O que **permanece** da identidade original (inegociável nas duas direções): stone quente, botões-pílula com afundamento de 1px, cards planos com `ring-1 ring-foreground/10` (sem shadow/border), destructive sempre *tinted*, charts monocromáticos, densidade `text-sm`, bordas translúcidas no dark.

A inconsistência de emoji apontada na v1 foi **resolvida no export v2** ("nunca emoji" em todos os documentos). Faxina pendente: `mvp/fonts/fonts.css` ainda importa Noto Sans JP do Google — resíduo do logo kanji v1, remover.

**Idioma (regra do dono, 2026-07-06):** PT-BR é **apenas apresentação** — labels de navegação e textos visíveis da UI. Todo o resto — código, variáveis, rotas, identificadores, nomes de componente, schema — é **inglês mandatório** (D16/CLAUDE.md). Ex.: label "Destilados" → rota `/distilled`; label "Atores" → identificador `persona`. A sessão que implementar views não traduz identificadores.

## Decisões de produto nascidas no design (pendentes de formalização)

| ID | Decisão | Formaliza em |
|---|---|---|
| D11 | **Destino** generaliza destinatário: pessoa (dono/convidado) OU sistema (webhook/arquivo). Nunca significa segundo datastore do Kubo — é entrega para fora | ADR na fase 2 (distribuição) |
| D12 | **Skills vivem no SurrealDB**, versionadas (salvar = nova versão imutável, nunca overwrite; estado "proposta pendente" para edição por flow na fase 4; nenhuma persona/worker tem escrita nas tabelas de skill — só o caminho autenticado do dono; gate humano como task nativa). Persona YAML (Git) segue declarando skills por nome; loader valida a referência | ADR na fase 3 (validado pelo advisor em 2026-07-04) |
| D13 (v2) | **Navegação em grupos, labels PT-BR** (v2, 2026-07-06): Painel · Conhecimento (**Destilados, Entidades, Fontes** — Entidades promovida a item próprio) · Trabalho (Fluxos, Execuções) · Distribuição (Destinos, Envios) · Catálogos (Integrações, **Atores**, **Modelos**). Labels são apresentação; identificadores seguem em inglês (regra de idioma acima) | referência direta na implementação das views |
| D13a | **"Atores" e "Modelos" mantidos** (decisão consciente do dono, 2026-07-06), com colisão registrada: "Modelos" (flow templates) × modelo-LLM. Mitigação obrigatória: nos cards/forms de Atores, o campo do LLM se rotula **"LLM"**, nunca "modelo" | implementação das views |
| D13b | **Toggle de visualização** (Lista / Duas colunas / Quadrados) via `window.ViewToggle` — padrão das telas de listagem | implementação das views |
| D14 | **Gate nunca é decisão de um clique**: botões do card abrem painel com contexto (o que as personas produziram, PR, review, budget); rejeitar exige motivo; decisão registrada no grafo | fase 3 (boards) |
| D15 | Tela **Configurações** (notificações de gate/falha, resumo diário, senha, tema) — adição consciente fora da IA original | fase 2+ |

## Arrumações

- `mvp/uploads/kobo-design-system.md` é cópia com nome antigo do doc canônico — **remover** (vai divergir; o canônico é `docs/kubo-design-system.md` após a sessão 0001).
- O namespace interno `KoboDesignSystem_*` nos JSX é identificador de build do export — aceito como está. Por isso, o critério `grep -ri kobo` da sessão 0001 **exclui `docs/design/mvp/`** (ver adendo no plano 0001).
