# Sessão 0015 — notas de execução + tabelas de paridade

> Complemento do plano `0015-boards-gates-epicb.md`. Aceite visual final (screenshots lado a
> lado) é no smoke gated (15.7, com o dreno parado + "pode executar" do dono).

## Estado dos marcos

| # | Marco | Estado |
|---|-------|--------|
| 15.1 | ADR-0018 esqueleto | ✅ commitado |
| 15.2 | Template `analysis-review` + `gates` no loader/snapshot | ✅ TDD (red→green) |
| 15.3 | Store: decisão na task, guarda de gate, `decide_gate`, `artifact=gate`, migration 0006 | ✅ |
| 15.4 | Runtime: produce-only, gate, notificação, `resume_gate`/`reject_gate` | ✅ + advisor |
| 15.5 | Credencial `kubo_rw` (connect_rw, compose, runbook §2d) | ✅ (EDITOR suficiente) |
| 15.6 | UI: lista → board → GateSheet → 2 POSTs (CSRF + staleness) | ✅ (disparo D38-b diferido) |
| 15.7 | Deploy + smoke físico gated | ⏳ **bloqueado no dono** (dreno parado + autorização) |
| 15.8 | ADR-0018 final (advisor) + paridade | ✅ (esta nota) |

## Desvios de escopo pré-acordados nesta execução

- **Disparo pela UI (D38-b, "Novo fluxo")** — 1º sacrifício do plano. Não implementado; o
  smoke dispara via `kubo flow run analysis-review "pergunta"` no CLI. **Nenhum link morto**
  (o botão "Novo fluxo" não é renderizado — D27).
- **Busca + toggle de view na lista** — 2º sacrifício. Só a lista, sem SearchBar/ViewToggle.

## Tabela de paridade — `FlowsScreen` (lista)

| Elemento do mockup | Status | Nota |
|---|---|---|
| PageHeader (título "Fluxos" + descrição) | igual | |
| Ação "Novo fluxo" | desvio: sacrifício de timebox | D38-b diferido; sem link morto |
| SearchBar | desvio: sacrifício de timebox | 2º sacrifício |
| ViewToggle (list/grid2) | desvio: sacrifício de timebox | só lista |
| Linha: ícone workflow + nome + badge template + badge gate + badge status | igual | nome = a pergunta do flow |
| Glifos do elenco | igual | derivados dos tasks (assigned_to) |
| "N tasks abertas · criado X" | igual | |
| "budget used/limit" | desvio: dado inexistente | budget não existe (ADR-0016 §VIII) |
| EmptyState (sem flows) | igual | com dica do comando CLI |
| EmptyState (busca vazia) | fora de escopo | busca cortada |

## Tabela de paridade — `FlowBoard`

| Elemento do mockup | Status | Nota |
|---|---|---|
| Voltar "Fluxos" | igual | |
| Nome do flow + badge template + badge "gate aberto" | igual | nome = a pergunta |
| Glifos do elenco | igual | |
| "budget used/limit · criado X" | desvio: dado inexistente | budget não existe; "criado" omitido no header |
| Colunas = estados do snapshot + contador | igual | |
| TaskCard: título + glifo persona + nome | igual | título sintetizado por persona |
| TaskCard gate: ring âmbar + "aguardando você" + Aprovar/Rejeitar | igual | |
| Badge "bloqueada" (dependência) | desvio: backend inexistente | não há bloqueio por dependência na fase |
| Badge "falhou" + mensagem no card | desvio: dado | falha aparece pela COLUNA `failed`; mensagem fica em Execuções |
| EmptyState (sem tasks) "Rodar agora"/"Retomar flow"/pausado | desvio: backend inexistente | pausa/retomar não existem; disparo é CLI |

## Tabela de paridade — `GateSheet`

| Elemento do mockup | Status | Nota |
|---|---|---|
| Painel modal lateral | desvio: limitação técnica | `<dialog>` nativo (modal do design system) em vez de slide-over custom — sem JS pesado; equivalência estrutural/de token |
| Header "Decisão de gate" + título + fechar | igual | |
| "O que está sendo pedido" | igual | a pergunta do flow (título do sheet) |
| "O que a(s) persona(s) produziu/produziram" | igual | o RELATÓRIO em texto plano (pre-wrap, autoescape) — nunca markdown→HTML |
| Link de PR | desvio: dado inexistente | pré-declarado no plano |
| Badge de budget | desvio: dado inexistente | pré-declarado |
| Textarea de motivo (rejeição) — obrigatório | igual | `required` + enforce no servidor (400) |
| Botões Aprovar / Confirmar rejeição | igual | |
| "Sua decisão fica registrada no grafo" | igual | |
| Lista "Fontes consultadas" | adição declarada | proveniência do relatório (arestas `consults`); não estava no mockup, mas serve o §VI |

## Segurança da tela (invariantes, não descobertas de PR)

- `deliverable.content` renderizado como TEXTO PLANO (`white-space: pre-wrap`, autoescape do
  Jinja), NUNCA markdown→HTML, NUNCA `|safe` — untrusted no consumo (ADR-0016 §II).
- `reason` é input renderizado: autoescape + cap na borda (`decide_gate`/pydantic).
- CSRF synchronizer token na sessão assinada; staleness 409; escrita só por `kubo_rw`, só nos
  2 handlers; 503 sem a credencial. Teste do no-op silencioso (`test_flows_write.py`) verde.
