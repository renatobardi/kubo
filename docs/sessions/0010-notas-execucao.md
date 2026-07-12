# Notas de execução — Sessão 0010 (UI Conhecimento + Execuções + fidelidade)

> Log da sessão de execução (CLI Opus + fable-advisor). Companheiro do plano
> `0010-ui-conhecimento.md`. Registra probes, decisões e a fila da 0011.

## M1 — Store reads (concluído, commit `feat(store): M1 ...`)

**Probe de projeção (E3) — decisão BINÁRIA: projeção vence.**
Contra container efêmero v3.1.5 + SDK 2.0.0, provado:
- `->derived_from->item.title` (título a 1 hop) — **funciona**.
- `->derived_from->item->from_source->source.canonical/kind` (fonte a 2 hops encadeados) — **funciona**.
- Card inteiro (título+fonte+data) numa query com `ORDER BY created_at` — **funciona**.
- Travessia de grafo volta **array mesmo para relação 1:1** (`title: ['X']`, `[None]` quando NULL) → `_unwrap` pega `[0]`, `[None]`→None→fallback 1ª linha do summary.
- `array::len(<-mentions)` + `ORDER BY` no alias computado — **funciona**, sem quirk.
- `read_entity`: `FROM $e<-mentions<-distilled` projetando `_CARD_COLS` — **funciona** (uma query).
- **`math::max` explode em datetime** (`Expected number`); **`time::max` funciona** → usado no badge de fonte (E4).

Não houve necessidade do fallback Python-compose. `read_distilled` segue com composição Python (não mexido).

## M7a — Probe read-only user (concluído, PROBE PASSOU)

Contra container efêmero v3.1.5 + SDK 2.0.0:

**(a) DEFINE USER … ROLES VIEWER** — funciona em `ON DATABASE` e em `ON ROOT`.

**(b) Signin do SDK 2.0.0:**
- Root: `signin({username, password})` — **sem** ns/db. Incluir ns/db no payload de root → `NotAllowedError` (falha!).
- User de DATABASE: `signin({username, password, namespace, database})` — **com** ns/db.
- User de ROOT (mesmo com ROLES VIEWER): `signin({username, password})` — **sem** ns/db, igual à forma atual do `client.py`.

**(c) Fail-closed — CONFIRMADO nas duas variantes.** Como viewer:
- SELECT funciona.
- UPDATE/CREATE retornam `[]` **sem levantar exceção** (negação SILENCIOSA no v3.1.5), mas o read-back como root prova que **o dado NÃO mudou** (status intacto, count intacto).
- ⇒ O teste fail-closed asserta **dado inalterado**, não *raises* (a negação não levanta).

**Duas rotas viáveis para M7b (decisão de ADR → advisor antes de cravar):**

| Rota | Impacto em `client.py` (strict) | Escopo |
|---|---|---|
| `ON DATABASE … VIEWER` | signin precisa de ns/db → ramo (root sem / DB-user com) | menor privilégio (só db kubo) |
| `ON ROOT … VIEWER` | **zero** (mesma forma de signin do root atual) | read-only em todos os NS (irrelevante: single-tenant) |

**DECISÃO (advisor, ADR-0014 amendment): Path A — ROOT-VIEWER.** Evita o branch permanente no caminho de auth (`client.py`), valor defensivo integral (nenhuma escrita possível). Path B (DB-scoped) rejeitado: o ganho de least-privilege é escopo vazio numa instância single-tenant; não vale um branch perpétuo no módulo mais sensível.

**Risco residual confirmado por sonda extra:** o ROOT-VIEWER LÊ `INFO FOR ROOT`, que expõe o PASSHASH argon2 do root (`INFO FOR DB` NÃO expõe). Mitigação: runbook MANDA senha root longa e aleatória (32+ chars) — argon2 + aleatória torna o crack offline irrelevante. Condição de validade da decisão: vale enquanto single-tenant (1 ns, 1 db); 2º namespace com dado de outra sensibilidade reabre a decisão.

**Quirk pinado (v3.1.5 + SDK 2.0.0):** negação de escrita é SILENCIOSA (retorna `[]`, não levanta). Companheiro do ADR-0005. Teste fail-closed: assert SELECT funciona + escrita NÃO altera dado (read-back) + tolera vazio OU exceção (não pina o silêncio).

**Amendment registra:** supersede da linha 101 do ADR-0014 (esboço `PERMISSIONS GRANT SELECT` → real é `ROLES VIEWER`); tripwire de escrita (1ª rota mutante NÃO reusa a viewer — no-op silencioso é footgun); rotação (`DEFINE USER OVERWRITE`/`REMOVE USER`) + passo de restore (RocksDB em volume novo não traz o user → runbook recria); higiene (DEFINE USER via `surreal sql` CLI, NUNCA pela store — structlog logaria a senha); só kubo-api aponta pro viewer (scheduler/distiller seguem com credencial de escrita).

Criação do user: **runbook one-time** (senha via env/getpass), NUNCA migration (invariante 8). `SURREAL_USER/PASS` do kubo-api apontam pro user read-only; fixture de integração cria o user no setup.

## Tabelas de paridade (DoD — conferir com screenshots lado a lado no smoke M8)

Status: *igual* / *desvio declarado (motivo)* / *fora*. Equivalência estrutural/de token, nunca pixel.

**Painel** (`HomeScreen.jsx` → `dashboard/index.html`)
| Elemento | Status |
|---|---|
| PageHeader com descrição | igual |
| 4 StatTiles ícone+clique (Fontes→/sources, Itens→/runs, Destilados→/distilled, Entidades→/entities) | igual |
| Card "Últimas execuções" (header + descrição + ação "Ver todas" + ícone por run + badge) | igual |
| Badge de run quota-aware (rate_limit→"quota") | igual (E6, consistente com Execuções) |
| Grid 2-col | desvio declarado (Fluxos fora → card único ocupa a linha) |
| Gate alert | fora (backend inexistente) |
| Card "Fluxos ativos" | fora (backend inexistente) |
| Label "Itens coletados (7d)" | desvio (mostramos total de itens, sem janela 7d — dado não existe) |

**Destilados lista** (`ConhecimentoScreen.jsx` tab Destilados → `distilled/list.html`)
| Elemento | Status |
|---|---|
| Busca (texto/semântica) | igual (já existia) |
| Card: título do item (E3) + fonte + data + preview do summary | igual |
| Glifo de fonte por kind | igual (youtube + default) |
| Badges de entidade no card | desvio (fora — a lista não projeta entidades por card; menções vivem na tela de Entidades) |
| View toggle | fora → 0011 (M6 deferido) |
| Datas dos 935 legados clusterizadas no dia do import | fato conhecido (ADR-0012), não bug |

**Fontes** (`FontesScreen.jsx` → `sources/list.html`)
| Elemento | Status |
|---|---|
| Glifo por kind | igual (youtube + default) |
| Nome/canonical | igual |
| Última coleta | igual |
| Itens acumulados | igual |
| Badge de recência (2 estados) | aproximação declarada (E4: fato "há Nd"/"sem coleta", não saúde) |
| "Adicionar fonte" | fora (E1, backend inexistente) |
| Detalhe de fonte | fora (E1) |
| Busca + view toggle | fora → 0011 |

**Entidades** (`ConhecimentoScreen.jsx` tab Entidades → `entities/list.html` + `detail.html`)
| Elemento | Status |
|---|---|
| Glifo por kind | igual |
| Nome + badge de tipo | igual |
| Contagem de menções | igual |
| Detalhe: destilados que mencionam (cards) | igual |
| Sparkline | fora (E2, dado inexistente) |
| Relações | fora (E2, dado inexistente) |
| Busca + view toggle | fora → 0011 |

**Execuções** (`ExecucoesScreen.jsx` → `runs/list.html`)
| Elemento | Status |
|---|---|
| Lista worker/quando/duração/itens | igual |
| Badge por error.kind (quota neutro ≠ falha destrutiva) | igual (E6, apresentação; status intacto) |
| Erro estruturado expansível | igual (via `<details>` nativo, sem JS) |
| Stats com fallback (itens) | igual (feed→items, distiller→distilled, senão omite) |
| Coluna "flow" | fora (E6, não existe fase 1) |
| Busca | fora → 0011 (2º sacrifício, ficou paginação) |

## M6 (polish) — DEFERIDO integralmente para a 0011

Decisão da thread (ponytail + marcador de sacrifício do plano): não gold-platear um vertical limpo.
- View toggle D13b (lista/grid) — o mais visível, mas declarado aproximação e nº 1 do sacrifício; adiciona JS + dívida "dois markups vs um".
- Total na paginação — exige contagem nova por tela.
- error.kind amigável no detalhe do distilled — exige `RunRef` carregar error_kind.

## Fila da 0011

- **M6 inteiro** (view toggle, total de paginação, error.kind amigável no detalhe) — ver acima.
- **Busca** em Execuções / Entidades / Fontes (as telas de lista sem busca desta sessão).
- **Catálogos** (Integrações/Atores/Modelos) — esperam D13a e backend.
- **Dívida nomeada "erro por fonte"** (E4): sinal de saúde real de coleta por fonte exige campo de erro por source no schema — fase do harvest, se o dono sentir falta.
- **Badges de entidade no card de Destilados** — se o dono quiser a paridade cheia do card do mockup, exige projetar menções por destilado na lista.
