# Sessão 0008 — Relatório de execução do M6 (destilação + grafo buscável)

> Execução em produção (kubo-test) em 2026-07-11. Branch `feat/0008-m6-distillation`.
> ADR canônico: `docs/adr/0013-destilacao-e-grafo-buscavel.md` (status **aceito**).

## Marcos entregues

| Marco | Entrega | Estado |
|---|---|---|
| 8.2 | chunking puro PT-BR | ✓ código + testes |
| 8.3 | cliente embedding Gemini REST (tripla ADR-0006) | ✓ código + testes |
| 8.4 | store `attach_chunks`/`distilled_without_chunks` + backfill | ✓ + **run vivo** |
| 8.5 | store `read_distilled` + CLI `kubo query`/`show` | ✓ + **prova vivo** |
| 8.6 | ApiExecutor + worker destilador (D6 como construção) | ✓ + **smoke vivo** |
| 8.7 | destilação agendada diária (09:00) | ✓ código (run em prod GATED) |
| 8.8 | ADR-0013 finalizado + este relatório | ✓ |

## Evidência empírica (runs vivos na kubo-test)

### Embedding — smoke cross-lingual (gate do backfill, ADR §VII)
Trios PT-BR mono (#1-10) + **cross-lingual (#11-13, pergunta PT ↔ summary EN)**:
**13/13 ordenados corretamente**, margem mínima +0.0624; cross-lingual +0.198/+0.169/+0.206.
**Conclusão: sem *language gap*** — o `gemini-embedding-001` não clusteriza por idioma;
pergunta PT recupera conteúdo EN. Habilitou o backfill do corpus misto sem trocar a tripla.

### Backfill dos 935 legados (ADR §VI/§VII)
`backfill_chunks.py`: **935/935 embedados, 0 falhas, 0 vazios**, ~5,5 min. Spot-check de
idioma (que **falsificou D20** — o corpus não é 100% PT-BR): **pt=571, en=355 (38%),
incerto=9** (ru/zh/km/id/ms — lixo de legenda multilíngue). Corpus vetorial é **multilíngue**.

### Prova dos 90 dias — `kubo query`/`show` (ADR §8.5)
- `kubo query "o que a Anthropic lançou recentemente?"` (pergunta PT) → **hit #1 é summary EN**
  ("Anthropic released Claude Fable...", distância 0.1815), intercalado com summaries PT por
  relevância. **Recuperação cross-lingual provada ponta a ponta**, não só no smoke.
- `kubo show <id> --provenance` → cadeia completa `distilled → item → source → run`
  (produced_by = `neon_import`). **Conhecimento com origem citável.**

### Smoke do destilador — pinagem do modelo (ADR §V)
Gate binário n=10 (8 reais + 2 canários de injection), pós-filtro verbatim de entidades:
- **`llama-3.3-70b-versatile`: 10/10 válido, 10/10 PT-BR, 0 malformado, 0 rate_limited,
  0 canary leak → PASS.** **Modelo pinado por evidência.**
- **`moonshotai/kimi-k2-instruct`: 10/10 `provider_errors`, 0 saída** — evidência de ID
  inválido/renomeado (**inferido dos erros; o catálogo do Groq NÃO foi re-listado** nesta
  sessão — sem a key aqui). O comparativo do plano não pôde rodar (§V já avisava que o
  catálogo depreacia rápido). A pinagem repousa na **evidência absoluta do llama** (10/10
  contra o gate binário), não num contraste relativo: o kimi caiu por config, não por
  qualidade — não há comparação de qualidade a registrar. **Follow-up opcional:** listar o
  catálogo Groq (`GET /openai/v1/models`) e, se houver ID sucessor do kimi, rodar o smoke
  nele para o contraste literal — não muda a pinagem do llama (já provado, já no código).
- Achado de segurança tratado: o 1º smoke vazou o canário de ENTIDADE. Análise (advisor +
  aval do dono) reenquadrou como **injection defense vs content trust** e adicionou **filtro
  verbatim de entidades** (defesa estrutural, ADR §V emenda) — o gate deixou de ser flaky.

## Estado da fila (insumo da fase 2)

- **935 destilados legados agora têm chunks** — buscáveis por `kubo query`.
- **~4.774 itens sem destilado** (5.709 itens − 935 destilados, relatório 0007) — o backlog
  bruto. O agendamento diário (09:00, `max_items=20`) processa 20/dia por ordem de id, então
  cobrir o backlog inteiro levaria ~8 meses no ritmo atual — **decisão da fase 2**: subir
  `max_items`, um one-off de backlog, ou destilar só um subconjunto curado.
- **Follow-up de qualidade (PRECONDIÇÃO do run vivo, não preferência):** parte dos 4.774 tem
  `content=""` (posts-link do Hacker News, ~536 no relatório 0007). O `items_without_distilled`
  os inclui; o destilador mandaria conteúdo vazio ao LLM. O risco não é só gastar chamada Groq:
  se o LLM **alucinar um summary a partir do vazio**, esse `distilled` persiste **com
  proveniência** e o item **sai do filtro para sempre** — conhecimento fabricado com carimbo de
  origem, o oposto do produto do M6. **Filtrar itens sem conteúdo** (na store ou no worker) é
  **precondição do "pode executar"** da destilação viva — não implementado nesta sessão (timebox;
  o gate do dono protege até lá).
- **Lixo estrutural do legado:** alguns summaries vieram embrulhados em JSON cru
  (`{"content_markdown":...}`, visto nos hits [3]/[4] do `kubo query`) — degradação conhecida
  (ADR §VII); limpeza implica re-embed (pelo no-op do `attach_chunks`), adiada.

## Custo

- **Embedding (Gemini):** ~½ milhão de tokens no backfill dos 935 ≈ **~US$0,07** (tier provavelmente
  pago — sustentou ~170 req/min). `kubo query` recorrente ≈ fração de centavo/busca. Registrado por
  transparência; troco na escala pessoal.
- **Chat (Groq):** free tier (D22). Smoke ~30 completions, grátis. Destilação diária no free tier.

## Follow-ups (dono confirmou)

1. **Rotacionar a `GEMINI_API_KEY`** — passou pelo chat em texto claro durante o setup. (A
   `GROQ_API_KEY` já estava no `.env` do servidor antes da sessão, **não** passou pelo chat — sem
   necessidade de rotação por vazamento.)
2. **Filtrar itens `content=""`** antes do run vivo de destilação (acima).
3. **Backlog de 4.774** — decidir ritmo/escopo na fase 2.
4. Limpeza do lixo do legado (re-embed, adiada): JSON cru (`{"content_markdown":...}`) **+ os 9
   summaries "incertos" de legenda multilíngue (ru/zh/km/id/ms)** que também foram embedados no
   índice — mesmo regime de re-embed (bloqueado pelo no-op do `attach_chunks` até haver caminho
   de replace de chunks, ADR §VII).
5. Monitorar `entities_filtered` no corpus real (se >20-30% de entidades legítimas caírem no
   filtro verbatim, reconsiderar matching — via novo ADR, ADR §V emenda).

## Gate pendente do dono

O **primeiro run de destilação em produção** (o job das 09:00, ou um disparo manual) está **GATED**
no "pode executar" do dono (plano 0008, tarefas do dono). O código está deployável; a destilação
viva não roda sem autorização explícita. **O "pode executar" pressupõe o follow-up (2)
implementado** (filtro de `content=""`): sem ele, o run vivo pode fabricar destilados a partir de
itens vazios — precondição sequenciada, não preferência.
