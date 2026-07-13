# Sessão 0014 — notas de execução (Trilha A entregue; Trilha B é do dono)

> Contrato: `docs/sessions/0014-quota-dreno-backlog.md`. Branch: `feat/0014-quota-drain`.
> Advisor (Fable 5) consultado: abordagem (A4 + retry-after/scope), ADR-0017, conclusão.

## O que foi entregue (Trilha A — código, TDD, gates verdes)

| # | Entrega | Onde | Testes |
|---|---|---|---|
| A1 | `retry-after` honrado no backoff (teto 120s; header numérico → espera+retenta; acima do teto → desiste; ausente/HTTP-date → exponencial) | `kubo/executors/api.py` (`_retry_after_seconds`/`_header_value`) | `tests/executors/test_api.py` (+5) |
| A2 | `RateLimitExhausted.scope` (`minute\|day\|unknown`) → distiller mapeia p/ `error.kind` `rate_limit_minute`/`rate_limit_day` (`unknown`→`rate_limit_exhausted`, retrocompat) | `kubo/errors.py`, `kubo/workers/distiller.py` | `tests/workers/test_distiller.py` (+2) |
| A3 | **Fix E2**: `EmbeddingError` no loop vira falha SISTÊMICA com parcial persistido (análoga a `RateLimitExhausted`) — antes estourava o run e perdia o parcial | `kubo/workers/distiller.py` | `tests/workers/test_distiller.py` (+1) |
| A4 | 3 reads read-only na store (invariante 2): `items_by_ids`, `list_distilled_with_items` (com `run_worker` = discriminador), `count_items_without_distilled` (métrica do dreno) | `kubo/store/knowledge.py` | `tests/store/test_knowledge.py` (+7) |
| A5 | Scripts one-off: `audit_sample.py` (B1), `distill_pilot.py` (B2), `drain_distill.py` (B3, E1) | `scripts/` | `tests/scripts/test_{audit_sample,distill_pilot,drain_distill}.py` (+19) |
| A6 | ADR-0017 (dreno one-off; emenda pontual à D22) | `docs/adr/0017-*.md` | — |

**Delta ao plano (implementação, não decisão — não reabre planejamento):** A4 materializou em **três** reads, não um. `items_by_ids` sozinho não serve a auditoria (precisa do par summary×content estratificado) — a alternativa era query crua no script (violação do invariante 2). `list_distilled_with_items` devolve `run_worker` (discriminador recente-vs-legado, robusto: `== "distiller"`); `count_items_without_distilled` é a métrica de progresso/reconciliação do dreno (server-side, sem puxar content). Avalizado pelo advisor.

**Regime diário intocado (critério de aceite):** `kubo/scheduler` e `_DISTILLER_MODEL` sem diff. O dreno constrói o PRÓPRIO `ApiExecutor` com modelo pinado.

**Gates:** ruff/format/pyright/detect-secrets/pip-audit/lock verdes; 543 testes; cobertura 98%.

## Trilha B — tarefas do dono (a sessão entregou o tooling; o dono opera)

### Passo 0 — verificação empírica do discriminador (advisor)
Antes da auditoria, confirme contra o kubo-test que `produced_by` distingue recente de legado:
```
SELECT count() FROM distilled WHERE array::len(->produced_by->run) = 0 GROUP ALL;  -- legados (sem run)
SELECT count() FROM distilled WHERE ->produced_by->run.worker CONTAINS "distiller" GROUP ALL;  -- recentes
```
Se os legados do import Neon tiverem run com `worker == "distiller"` (improvável), o discriminador precisa de ajuste — avise. Caso contrário, siga.

### B1 — Auditoria (gate de qualidade do llama existente)
```
# no ambiente com SURREAL_URL do kubo-test (Tailscale):
uv run python scripts/audit_sample.py            # gera audit_sample.local.md (git-ignorado, NÃO commitar)
```
Julgue com a rubrica embutida no doc (alucinação = ELIMINATÓRIO por estrato). Só o **agregado** (contagens + veredito) entra aqui nas notas depois. **Decisão binária:** estrato `recente` reprovado por alucinação ⇒ GO do dreno morre até novo piloto.

### B2 — Piloto (candidato vs baseline llama)
```
OPENROUTER_API_KEY=... uv run python scripts/distill_pilot.py \
    --models openrouter/meta-llama/llama-3.3-70b-instruct     # gera distill_pilot.local.md
```
Aponte o vencedor. Se o vencedor não for o default, **edite `_DRAIN_MODEL` em `scripts/drain_distill.py` e abra PR** (o pin é o gate humano).

### B3 — Dreno (dias seguintes, FORA da sessão) — checklist pré-1º-batch
- [ ] **Spend limit** configurado na key do OpenRouter.
- [ ] Limites reais do Gemini verificados no AI Studio (E5 — teto é a RPD do embedder, ~1K/dia; reset meia-noite do Pacífico).
- [ ] Decisão **E3** tomada: o digest das 09:30 pode despejar backlog velho no Telegram após o 1º batch (avançar watermark / pausar digest / aceitar).
- [ ] **E4**: nunca rodar batch na janela **09:00–09:35** (colisão com a entry agendada = duplicatas; `insert_distilled` não é idempotente).
```
OPENROUTER_API_KEY=... GEMINI_API_KEY=... uv run python scripts/drain_distill.py \
    --batch-size 25 --max-batches 10       # supervisione; ajuste --max-batches ao teto do Gemini do dia
```
O dreno para sozinho em: backlog vazio (`done`), erro sistêmico do dia (`error`, retomável) ou stall (`stuck`, precisa atenção). **Reconciliação final** (N destilados, custo real do dashboard OpenRouter, rejeitados, delta) → nota de fechamento na seção "Reconciliação" do ADR-0017.

## Notas
- Docs de auditoria/piloto contêm **conteúdo coletado**: `*.local.md` é git-ignorado; nunca commitar.
- A casca de I/O dos 3 scripts é validada no 1º uso supervisionado contra o banco vivo (precedente `backfill_chunks.py`); a camada pura tem testes unitários.
