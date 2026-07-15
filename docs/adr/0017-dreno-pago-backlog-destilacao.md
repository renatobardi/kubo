# ADR-0017 — Dreno pago one-off do backlog de destilação (emenda pontual à D22)

> Status: **aceito** · Data: 2026-07-13 · validado pelo advisor (Fable 5)

## Contexto

O regime diário de destilação roda no Groq free tier (D22, pinado no ADR-0013 §V) com entrada `schedules.yaml` ~09:00. O backlog acumulou ~3.2k itens sem destilado. Duas motivações do dono (D35):

1. **Preferência por velocidade:** pagar (~US$2) para drenar em ~4 dias, em vez de esperar ~5 meses no free tier (backlog ÷ capacidade diária ~5–15 itens).
2. **Dúvida sobre qualidade:** o smoke da sessão 0008 foi gate de "não-lixo" (JSON válido, PT-BR, 0 canários vazados), nunca de qualidade fina. Decisão: submeter o existente e o candidato a auditoria estratificada (8 distiller-recente + 4 legado PT + 4 legado EN) com rubrica eliminatória do dono.

**Contexto conexo — sustentabilidade do regime diário:** o backoff cego contra a janela de TPM de 60s do Groq morria sem recuperação, e o backlog crescia. A correção permanente (honrar `retry-after`) é decisão deste ADR — ver §0 — porque a mesma emenda ("dreno one-off + regime diário durável") governa os dois.

## Decisão

### 0. Fix permanente do regime diário: `retry-after` honrado + taxonomia de `scope`

Única mudança PERMANENTE de comportamento do runtime desta sessão (A1/A2) — registrada como decisão, não contexto, porque durabiliza o regime diário que o dreno preserva:

- O executor (`kubo/executors/api.py`) passa a extrair o header `retry-after` da exceção do LiteLLM (só valor **numérico** atravessa a fronteira — §VIII; corpo cru nunca), com **teto de espera 120s**:
  - `retry-after` numérico ≤ teto → espera e retenta (janela de minuto do Groq, TPM 60s — recuperável).
  - `retry-after` acima do teto → desiste imediato (`scope="day"`, TPD/RPD — retentar no run não recupera).
  - ausente / HTTP-date (não-numérico) → cai no backoff exponencial legado (`scope="unknown"`).
- `RateLimitExhausted` ganha `scope` (`minute`/`day`/`unknown`); o distiller mapeia para `error.kind` visível em Execuções: **`rate_limit_minute`**, **`rate_limit_day`** e **`rate_limit_exhausted`** (o `unknown`, kind histórico preservado por retrocompat).
- Efeito: o run único das 09:00 processa a fila do dia (~20 itens = ≤75K tokens, dentro do TPD de 100K) sem morrer contra a janela de minuto. Nenhuma mudança em `schedules.yaml`/`_DISTILLER_MODEL`/`kubo/scheduler/`.

### I. Dreno é operação ONE-OFF, não mudança permanente do regime

- **D35 — Dreno APROVADO.** Modelo pago barato: candidato natural = `llama-3.3-70b` via OpenRouter (~US$0,10/M tokens → dreno ~US$1,50–2 total). Conta Groq **NÃO sofre upgrade** — free tier diário preservado por construção (não por disciplina).
- **E1 — Pontualidade estrutural, não procedural.** O dreno é script one-off `scripts/drain_distill.py` (precedente `backfill_chunks.py` da ADR-0013 §VII) que constrói seu próprio `ApiExecutor` com modelo **pinado como constante no script** (`_DRAIN_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct"`). Troca de modelo = editar o script → PR → gate humano (jamais arg de CLI — CLI livre reabriria a porta que o hardcode fechou). **Regime diário permanece Groq por construção**, intocado: nenhuma mudança em `_DISTILLER_MODEL`, `schedules.yaml` ou `kubo/scheduler/`.
- Cada batch do dreno abre um `run` normal → proveniência automática (flow_id, task_id, run_id, distilled-ids) e reconciliação de graça.

### II. Três gates de qualidade (D36): auditoria → piloto → dreno supervisionado

| Gate | Critério |
|---|---|
| **B1 — Auditoria** | Amostra estratificada **8+4+4** do backlog (16 items): 8 destilados recentes (llama via Groq, decidem GO/NO-GO do dreno) + 4 legado PT + 4 legado EN (decidem re-destilação futura). **Rubrica fixada pelo dono ANTES de entregar o doc:** por item — alucinação (binário, ELIMINATÓRIO) · fidelidade/PT-BR-natural/entidades (aprova·ressalva·reprova) · nota livre. Agregação **POR ESTRATO:** 1 alucinação = estrato reprovado; ≥80% aprova = ok. Julga-se tendência, não caso isolado. **Binária:** auditoria reprovar o estrato do llama recente → dreno morre inteiro. |
| **B2 — Piloto** | Mesmos 16 itens no candidato via LiteLLM (`openrouter/...` — só env key, zero mudança no executor). Se a auditoria aprovar o llama, o piloto é A/A de formalidade (próprio llama). Relatório conta `malformed` por candidato. Dono aponta vencedor. |
| **B3 — Dreno** | Batches de 25–50 itens, supervisionados com "pode executar" por dia. Checklist pré-1º-batch: spend limit configurado na key OpenRouter · limites Gemini verificados no AI Studio · E3 (digest durante dreno) decidida · janela 09:00–09:35 respeitada (evitar duplicatas). |

### III. Emendas do advisor ao regime (E1–E5)

- **E2 — Fix obrigatório ANTES de gastar:** `EmbeddingError` no loop do distiller hoje estoura o run e perde payloads já destilados (no Groq free custa zero; no pago é dinheiro re-gasto a cada re-run). `EmbeddingError` vira falha sistêmica com parcial gracioso, análoga a `RateLimitExhausted`: o worker para o loop, PERSISTE o que já foi destilado e devolve `error.kind="embedding_failed"` — o `run` fecha em erro estruturado (status `error`) com os payloads parciais gravados, nunca explode perdendo o lote.
- **E3 — Decisão pré-operacional:** digest de 09:30 vai despejar backlog velho no Telegram após 1º batch (watermark `last_dispatch_watermark` avança sobre até 50 itens/dia de conteúdo antigo). Decidir ANTES: avançar watermark manualmente / pausar digest durante dreno / aceitar despejo. Operacional, não surpresa.
- **E4 — Colisão dreno × entry das 09:00:** `items_without_distilled` é ORDER BY sem lock e `insert_distilled` não é idempotente — job agendado no meio de batch pega MESMOS itens = duplicatas reais. **Regra operacional:** batch nunca na janela 09:00–09:35 (ou comentar entry durante dreno, reversível).
- **E5 — Teto real = Gemini, não LLM:** worker embedda 1 chamada/item → 3.2k requests ÷ ~1K RPD free Gemini ≈ **3–4 dias**. RPD reseta **meia-noite Pacífico** (≠ Groq UTC). Quota é por projeto: **em dia de dreno, `kubo query`/busca da UI podem 429**. Não reescrever pipeline para batch cross-item (mudança de worker/store por one-off não se paga).

### IV. Re-destilação de legados reprovados = follow-up fora do dreno

Auditoria reprovar legados (PT ou EN) por qualidade → re-destilação fica FORA do dreno. **Motivo estrutural:** não existe caminho de *replace* de `distilled`/`chunks` (ADR-0013 §VII — `attach_chunks` é mão-única, no-op se chunk já existe). Logo:
- Dreno = **insert** de coisa nova (caminho provado da ADR-0013 backfill).
- Replace de chunks = caminho inexistente; pré-requisito de qualquer re-destilação é implementar `replace_distilled` com preservação de proveniência (`produced_by`, `mentions`).
- **Registro explícito:** follow-up pendente com pré-requisito nomeado (implementar replace → re-destilar legados reprovados).

## Consequências

### Positivas

- **Backlog drenado em dias** (não meses), barreira psicológica removida.
- **Qualidade auditada antes de gastar** (rubrica do dono, gate binário por alucinação).
- **Regime diário intocado** (scheduler, `_DISTILLER_MODEL`, `schedules.yaml` zerados em diff → prova estrutural).
- **Proveniência automática** (cada batch = run normal, distilled-ids rastreáveis até source).
- **Fix colateral (E2):** EmbeddingError persiste parcial → elimina re-gasto em falhas de embedding (técnica, valia ganha).

### Trade-offs

- **Custo ~US$2** (operador responsável por spend limit).
- **Teto real é Gemini:** ~1K RPD free → **3–4 dias de execução**, não parallelizável. Em dia de dreno, `kubo query` pode tomar 429.
- **Digest pode despejar backlog velho** no Telegram (E3 — decisão pré-operacional do dono).
- **Colisão E4:** janela 09:00–09:35 evitada operacionalmente (não construída; procedimento manual se falhar).
- **Re-destilação de legados adiada** com pré-requisito de replace (clean-up futuro, não urgência).

### Estruturação do risco

O plano enumera 3 decisões binárias pré-fixadas:
1. Auditoria reprovar llama recente por alucinação → dreno morre inteiro.
2. Limites Gemini muito abaixo de 1K RPD → reavaliar (dreno viraria semanas; batch cross-item ou Tier 1 Gemini entram na conversa do dono).
3. Retry-after do Groq sistematicamente ausente/mentiroso → consulta extraordinária ao advisor.

## Alternativas rejeitadas

- **Esperar ~5 meses no free tier** — rejeitada por D35 (dono prioriza velocidade sobre custo).
- **Upgrade permanente da conta Groq** — rejeitada por D35 e invariante: free tier diário é não-negociável; dreno é janela temporal, não infraestrutura.
- **Modelo do dreno como arg de CLI** — rejeitada por E1: reabriria o gate humano que o hardcode fechou; rig procedural não substitui construção.
- **Re-destilar legados reprovados neste dreno** — rejeitada por IV: não existe replace de chunks; pré-requisito explícito enfileirado.
- **Batch cross-item de embedding** — rejeitada por E5: mudança de worker/store por one-off não se paga; teto Gemini é a restrição real.
- **Modelo por fallback declarável** — rejeitada: fase 3 (persona com fallback); dreno é pinagem hardcode (decisão).

## Reconciliação (fechamento — 2026-07-15)

Levantada via SSH sobre o grafo de PRD (kubo-test) no fechamento da sessão 0016b; o custo real vem do dashboard do OpenRouter (dono, antes do merge).

| Campo | Valor |
|---|---|
| **items_without_distilled (escopo do dreno)** | ~3.239 antes (registro da 0014) → **6 depois** (live) — o dreno destilou ≈3.233 itens |
| **N destilados (cumulativo, live)** | **4.179** `distilled` / **5.734** `item` totais |
| **Content-vazio (fora do escopo, filtro)** | **1.549** itens sem `derived_from` E `content` vazio — NUNCA candidatos ao dreno (reentram se um harvest futuro preencher o content); não são resíduo |
| **Custo real** | — USD (dashboard OpenRouter — a preencher pelo dono antes do merge) |
| **Residuais (6) — diagnóstico** | Cauda IRREDUTÍVEL de conteúdo não-destilável, não falha do dreno: 1 transcrição só-música (`🎵`, len=1); transcrições longas de baixo sinal (YouTube "Amazon travel gadgets" EN 16.5k; podcast/ad PT 11.4k; Hipsters #47/#412). Produzem `malformed` no LLM → skip → reentram no funil (ADR-0013 §V, comportamento por desenho). Follow-up opcional: prefiltro de qualidade de conteúdo ou revisão manual — não urgente. |
| **Auditoria (B1)** | Estrato llama recente: ✓ APROVADO (GO do dreno) · legados PT/EN: registrados na 0014 (re-destilação = follow-up §IV, aguarda replace de chunks) |
| **Piloto (B2)** | Vencedor: **llama via OpenRouter** (A/A de formalidade, dono apontou) |
| **Veredito geral** | ✓ **Dreno bem-sucedido** — backlog destilável drenado a 6 residuais irredutíveis; regime diário intocado (Groq free, a run das 12:00 de 2026-07-15 acusou `rate_limit_exhausted`, TPD esperado pós-dreno) |

**Notas de fechamento:** (a) o "antes" exato do escopo do dreno é o registro operacional da sessão 0014 (~3,2k) — o dono confirma o número de partida se quiser precisão além da ordem de grandeza; o "depois" (6) e os cumulativos (4.179/5.734/1.549) são leitura viva do grafo. (b) O custo real do OpenRouter é o único campo que depende de dado fora do grafo — o dono o traz antes do merge. (c) Follow-up ainda aberto (§IV): **re-destilação de legados reprovados** aguarda implementação de `replace_distilled` com preservação de proveniência (pré-requisito nomeado).

---

**Referências:** Plano sessão 0014 (docs/sessions/0014-quota-dreno-backlog.md, D35–D36 dono + E1–E5 advisor); ADR-0013 §V e §VII (regime diário Groq, backfill one-off); ADR-0009 (contrato worker); ADR-0015 (dispatch).
