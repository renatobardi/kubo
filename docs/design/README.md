# Design MVP — nota de decisões

> **Data:** 2026-07-04 · **Status:** ratificado pelo dono
> **Conteúdo:** `mvp/` é o export do Claude Design — design system (tokens, componentes, guidelines) + app navegável (`mvp/ui_kits/kubo-app/`).

## O que este artefato é (e não é)

- **É referência de implementação** para as views do Kubo (fases 2+): arquitetura de informação, hierarquia, componentes, estados, tokens.
- **Não é código de produção.** O export é React/JSX; o Kubo implementa em FastAPI + HTMX + Jinja + Tailwind 4 usando **os mesmos tokens** (`mvp/tokens/*.css` são CSS puro, portáveis). Nenhuma dependência entra por esta porta.
- **Não altera o roadmap:** UI segue fora do caminho crítico da fase 1 (M1–M6). Boards/kanban são fase 3; este material espera lá.

## Identidade visual — Direção B ratificada (decisão do dono, 2026-07-04)

O mockup diverge deliberadamente do design system extraído do Valmis, e o dono ratificou a divergência. **A Direção B é a identidade canônica do Kubo.** Ao materializar `docs/kubo-design-system.md` (sessão 0001), aplicar esta emenda:

| Item | Direção A (Valmis, rejeitada) | **Direção B (canônica)** |
|---|---|---|
| Primária | Âmbar queimado `oklch(0.555 0.163 49)` | **Preto mono** `oklch(0.216 0.006 56)` (dark inverte p/ quase-branco `0.92`) |
| Títulos | Noto Serif (`--font-heading`) | **Inter em tudo** (Noto Serif preservada em `--font-serif` só como opção de revert) |
| Identidade de persona | Emoji de lista preset | **Glifos Lucide monocromáticos** em círculo muted (`PersonaGlyph`); a lista de emojis permanece como capability do `AgentAvatar`, sem uso no produto |
| Logo | — | **Kanji 智 em tile** + "Kubo" em Inter; alternativas registradas: 匠 / 創 / 結 / 織 / 巧 / 房 |
| Tagline | — | "The art of getting things done" (raiz: 段取り *dandori*) |

O que **permanece** da identidade original (inegociável nas duas direções): stone quente, botões-pílula com afundamento de 1px, cards planos com `ring-1 ring-foreground/10` (sem shadow/border), destructive sempre *tinted*, charts monocromáticos, densidade `text-sm`, bordas translúcidas no dark.

Nota de consistência: o README interno do export se contradiz (diz "emoji everywhere" e "nunca emoji"). **Vale o glifo Lucide** — emoji não é identidade de persona na Direção B.

## Decisões de produto nascidas no design (pendentes de formalização)

| ID | Decisão | Formaliza em |
|---|---|---|
| D11 | **Destino** generaliza destinatário: pessoa (dono/convidado) OU sistema (webhook/arquivo). Nunca significa segundo datastore do Kubo — é entrega para fora | ADR na fase 2 (distribuição) |
| D12 | **Skills vivem no SurrealDB**, versionadas (salvar = nova versão imutável, nunca overwrite; estado "proposta pendente" para edição por flow na fase 4; nenhuma persona/worker tem escrita nas tabelas de skill — só o caminho autenticado do dono; gate humano como task nativa). Persona YAML (Git) segue declarando skills por nome; loader valida a referência | ADR na fase 3 (validado pelo advisor em 2026-07-04) |
| D13 | **Navegação em grupos** contando a história: Home · Conhecimento (Conhecimento, Fontes) · Trabalho (Flows, Execuções) · Distribuição (Destinos, Envios) · Catálogos (Integrações, Personas, Templates) | referência direta na implementação das views |
| D14 | **Gate nunca é decisão de um clique**: botões do card abrem painel com contexto (o que as personas produziram, PR, review, budget); rejeitar exige motivo; decisão registrada no grafo | fase 3 (boards) |
| D15 | Tela **Configurações** (notificações de gate/falha, resumo diário, senha, tema) — adição consciente fora da IA original | fase 2+ |

## Arrumações

- `mvp/uploads/kobo-design-system.md` é cópia com nome antigo do doc canônico — **remover** (vai divergir; o canônico é `docs/kubo-design-system.md` após a sessão 0001).
- O namespace interno `KoboDesignSystem_*` nos JSX é identificador de build do export — aceito como está. Por isso, o critério `grep -ri kobo` da sessão 0001 **exclui `docs/design/mvp/`** (ver adendo no plano 0001).
