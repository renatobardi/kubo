# Sessão 0011 — UI: fidelidade de fase 2 (shell + padrões transversais + épicos)

> **Status:** rascunho consolidado do review página a página do dono sobre o app deployado
> (kubo-test, 100.66.254.24:3900) contra o mockup (`docs/design/mvp/ui_kits/kubo-app/` + screenshots).
> **Origem:** 0010 mergeada (#21) + branch #22 (shell parcial: breadcrumb/PageHeader/rodapé já feitos).
> **Pendente:** validação do advisor (decisões de dado/segurança) antes de cravar.

## Missão

Fechar a fidelidade visual e funcional das telas da fase 2 ao mockup, resolvendo os desvios que o
review do dono levantou — **sem fabricar dado** (o invariante que gerou os cortes E1/E2 continua de
pé). Entregar os **três padrões transversais** que o mockup assume (busca, view toggle, paginação),
terminar o **shell**, e **nomear como épicos de backend** os dois itens que exigem dado que a fase 1
não tem (relações/timestamp de menção; escrita na UI).

## Princípio que rege tudo (não negociável)

A regra de fidelidade corta nos dois sentidos, e **nunca** justifica inventar dado. Onde o mockup
mostra algo que o backend não produz (sparkline de menções, relações, "adicionar fonte"), a resposta
é **placeholder honesto** ("sem dados ainda" / "em breve") + um **épico de backend** separado — nunca
um gráfico/lista/registro fabricado com carimbo real (viola os invariantes de integridade do projeto).

## Decisões do dono necessárias (resolver antes de executar)

| # | Questão | Recomendação da thread |
|---|---|---|
| **D-a** | Sparkline "menções ao tempo" + card "Relações" no detalhe de entidade (cortados no E2 por dado inexistente) | **(c)** placeholder honesto agora + **EPIC-A** (produtor de relações + timestamp de menção) depois |
| **D-b** | Botão "+ Adicionar fonte" (cortado no E1: UI read-only, sem backend) | **(c)** botão desabilitado "em breve" agora + **EPIC-B** (escrita na UI) depois |
| **D-c** | Views em Execuções (mockup só tem `allowed=['list']`) | **(a)** seguir o mockup: só lista, sem grid (não inventar além do mockup) |
| **D-d** | Teto do seletor de tamanho de página (`_MAX_PAGE=100` hoje) | 50/100 (cabem no teto atual); >100 exige subir `_MAX_PAGE` conscientemente na store |

## Marcos

### M1 — Shell (termina o #22; sem backend)
- **[S1]** Ícone por item de nav (Painel=home, Destilados=book-open, Entidades=network, Fontes=rss,
  Execuções=activity). Mapa ícone→rota no `NAV` + render no `base.html`.
- **[S2]** Recolher-menu: toggle (▢ no topo-esquerdo) → sidebar só-ícones, estado em `localStorage`,
  tooltip no hover. Client-side (JS + CSS).
- **[S3]** Logo solto: trocar o `favicon.svg` (tem `<rect fill="#1c1917">`) pela **sakura de linha**
  inline, fundo transparente, theme-aware via `--sakura-ink`/`--sakura-petal` (já no input.css).
  Portar de `components/brand/Logo.jsx` (variante `mark`). Vale em `base.html` E `login.html`.

### M2 — Padrões transversais, nascendo APLICADOS (fatias verticais, não camada)
Correção do advisor: **nada de "componente pronto sem tela"** (isso é camada horizontal — o
CLAUDE.md manda fatias verticais finas). Cada padrão nasce numa tela **ponta a ponta** (store → macro
→ tela), valida no primeiro uso real, e só então rola pras outras. Cada rollout é um PR que é uma
fatia funcionando.
- **[P-pag] Paginação completa** — nasce em **Destilados** (store `count` + macro prev/next +
  "página X de Y" + seletor 50/100, ponta a ponta), depois rola pra **Entidades** e **Execuções**.
  Substitui o peek+1. `size` de query param, clampado (D-d).
- **[P-view] View toggle D13b** — nasce em **Fontes** (a REFERÊNCIA: os 3 modos list/grid2/squares),
  depois rola pra **Entidades** (list/grid2) e **Destilados** (só list). Client-side (classe no
  container + `localStorage`, reaplica no load); markup alternativo por tela.
- **[P-busca] Busca — NÃO é um componente, são DOIS mecanismos** sob um input compartilhado (só o
  markup do input vira macro; **nunca** uma abstração de comportamento / "search framework"):
  **server-side** nasce em **Entidades** (store filtra por nome/kind normalizado), rola pra
  **Execuções** (worker/status); **client-side** em **Fontes** (lista curta). Destilados mantém a
  busca semântica existente.

### Estado final por tela (aceite da paridade)
- **Destilados**: [P-pag]; view só `list` (mockup).
- **Entidades (lista)**: [P-busca server-side] + [P-pag] + [P-view `list/grid2`].
- **Fontes**: [P-busca client-side] + [P-view `list/grid2/squares`]. (sem paginação — lista curta)
- **Execuções**: [P-busca server-side] + [P-pag]; view = decisão D-c (recomendado: só lista).
- **Trap busca×paginação**: quando busca e paginação coexistem (Entidades, Execuções), o "X de Y"
  reflete a busca ATIVA — o count usa o MESMO predicado da lista (ver superfície da store). Trocar o
  tamanho de página reseta pra página 1.

### M4 — Placeholders honestos (dado inexistente; sem fabricar)
- Detalhe de entidade: bloco "Menções ao longo do tempo" e card "Relações" com estado **"sem dados
  ainda"** (estrutura do mockup, zero número inventado). Some quando EPIC-A entregar os dados.
- Fontes: botão "+ Adicionar fonte" **desabilitado** com "em breve". Ativa quando EPIC-B entregar.

### Épicos de backend (fora desta sessão; cada um com ADR + advisor próprios quando atacado)
- **EPIC-A — Produtores de dado do grafo de entidade:** extração de relações (`relates_to`) no
  distiller (chamada LLM adicional, custo/qualidade próprios) + timestamp por menção. Desbloqueia
  sparkline + card de relações reais. Fase harvest/distiller. **Nota crítica:** entregar o CÓDIGO não
  entrega os DADOS — timestamps de menção só passam a existir daí pra frente (sem backfill: o
  `created_at` legado = dia do import, ADR-0012/0013). O placeholder "sem dados ainda" **persiste
  semanas** após o merge do EPIC-A, até acumular histórico real. Registrar isso no épico para não
  virar "cadê o gráfico?" no dia seguinte.
- **EPIC-B — Escrita na UI (add-source e além):** form + rota POST + **token CSRF** (dispara o tripwire
  do ADR-0014) + backend de registro/validação de source (e ligação com a coleta). **A QUESTÃO CENTRAL
  do ADR** (advisor): o processo web ganha uma credencial de ESCRITA no banco, OU a UI posta para uma
  rota que **delega ao caminho de escrita já existente** (API/worker)? A 1ª quebra a propriedade mais
  valiosa do ADR-0014 (o processo exposto ao browser é fisicamente incapaz de escrever) — a delegação
  preserva. Essa pergunta é o coração do ADR do EPIC-B; nunca nascer com a resposta assumida. Mexe na
  segurança — ADR próprio antes de qualquer código.

## Store — superfície nova prevista (strict, TDD)
- Contagens **parametrizadas pelo MESMO filtro da busca** — não `count_entities()` e sim
  `count_entities(query=None)` compartilhando o WHERE com `search_entities`; idem runs. Senão o
  "X de Y" mente durante a busca (bug que passa em teste isolado e aparece em prod — alerta do advisor).
  Base já existe: `_count()` em `knowledge.py` (usado pelo dashboard) — superfície menor do que parece.
- `search_entities(query, ...)` — filtro por nome/kind normalizado, paginado (+ count irmão).
- `search_runs(query, ...)` — filtro por worker/status, paginado (+ count irmão).
- Total de distilled = `count` simples (Destilados usa a busca semântica, não este filtro).
- (avaliar subir `_MAX_PAGE` só se D-d pedir >100.)

## Desvios a MANTER (não mexer)
- Nav só com o implementado (D27, zero link morto) — mockup mostra Fluxos/Distribuição/Catálogos; nós não.
- Card de destilado COM ícone à esquerda (fiel ao JSX canônico — não era desvio).
- Busca global do topo = visual, leva a Destilados (fase 1).
- Sem markdown em summary; sem `|safe`; toda sessão continua GET até EPIC-B (aí CSRF entra).

## Pontos de consulta ao advisor
1. **Este plano** (abordagem: 3 componentes reutilizáveis vs por-tela; ordem M1→M4; épicos adiados).
2. **D-a / D-b**: placeholder-agora + épico-depois é o certo, ou o dono quer o épico já?
3. **EPIC-A e EPIC-B** ganham ADR próprio quando atacados (extração de relações; escrita/CSRF/credencial).
4. Subir `_MAX_PAGE` (se D-d pedir >100).

## Critérios de aceite
- [ ] Shell: ícones na nav, recolher funcional, logo solto theme-aware (sem quadrado) — base + login.
- [ ] 3 componentes reutilizáveis (paginação/view/busca) com teste, aplicados às telas certas.
- [ ] Placeholders honestos onde falta dado (nada fabricado).
- [ ] Cada tela confere com o mockup (tabela de paridade atualizada; screenshots lado a lado).
- [ ] Épicos A e B registrados como trabalho de backend com ADR próprio pendente.
- [ ] Gates verdes; cobertura mantida; PR(s) conforme.

## Escopo negativo
- NENHUM dado fabricado (sparkline/relações/fonte reais só via épicos com dado real).
- NENHUMA escrita na UI nesta sessão (add-source fica placeholder; escrita = EPIC-B).
- Não estender view toggle a Execuções sem a decisão D-c.
- Não passar de `_MAX_PAGE` sem decisão D-d.
