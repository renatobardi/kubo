# Sessão 0014 — Quota Groq + dreno do backlog (com gates de qualidade)

> **Status:** aprovado pelo dono (2026-07-13, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas para a SESSÃO (trilhas A+B abaixo); a EXECUÇÃO do dreno NÃO pertence à sessão — batches rodam nos dias seguintes como operação supervisionada (~3–4 dias, ditados pelo Gemini)
> **Estrutura:** 1 PR — branch `feat/0014-quota-drain` (D16)
> **Pré-requisito:** merge do PR #29 (0013) — o smoke do relatório fecha antes desta sessão começar
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

Duas coisas: (1) tornar o regime diário **sustentável no Groq free pra sempre** (honrar `retry-after`, discriminar quota de minuto × dia); (2) **drenar o backlog de ~3.2k itens sem destilado** com modelo pago barato, atrás de três gates de qualidade do dono. Motivação declarada do dono: dúvida sobre a qualidade do que o llama-3.3 já gerou — o smoke da 0008 foi gate de não-lixo, nunca de qualidade fina; e preferência explícita por pagar a esperar (~5 meses grátis vs ~4 dias + ~US$2).

## Decisões do dono

- **D35:** dreno pago aprovado — velocidade sobre espera. Crédito existente (OpenRouter US$5 preferido; DeepSeek US$2 provavelmente NÃO cobre o dreno completo — ~US$4,5 no deepseek-chat). **Conta Groq NÃO sofre upgrade** — free tier diário preservado por construção.
- **D36:** três gates de qualidade, todos do dono: auditoria do existente → piloto lado a lado → dreno batch a batch.
- **ADR-0017** registra a emenda pontual à D22 (dreno one-off; regime diário permanece Groq/D22 intacto).

## Fatos verificados (doc oficial Groq, 2026-07-13)

llama-3.3-70b free: RPM 30 · RPD 1K · **TPM 12K** · **TPD 100K**; TPD reseta meia-noite UTC; 429 traz header `retry-after`. Diagnóstico: o backoff atual (0.5/1/2s, 3 tentativas cegas) morre contra janela de TPM de **60s** — aritmética, não quota. Com retry-after honrado, o run único das 09:00 processa os ~20 itens em ~10min. Inflow diário (5–15 itens ≈ ≤75K tokens) cabe no TPD com folga.

## Emendas do advisor (GO com E1–E5, todas incorporadas)

- **E1 — O dreno NÃO toca scheduler nem `_DISTILLER_MODEL`.** Script one-off `scripts/drain_distill.py` (precedente `backfill_chunks.py`) constrói `ApiExecutor(model=<aprovado>)` explicitamente e chama `run_worker` em loop. Modelo **pinado como constante no script** (entra por PR = gate humano; arg de CLI livre reabriria a porta que o hardcode fechou). A "config temporária a reverter" **deixa de existir**: o regime diário permanece Groq por construção, não por disciplina. Cada batch abre um `run` normal → proveniência e reconciliação de graça.
- **E2 — Fix obrigatório ANTES do dreno: `EmbeddingError` no loop do distiller vira falha sistêmica com parcial** (análoga a `RateLimitExhausted`). Hoje ela estoura o run e os payloads já destilados se perdem — no Groq custa zero; no dreno é **dinheiro pago re-gasto a cada re-run**. A mudança pequena mais valiosa da sessão.
- **E3 — O digest de 09:30 vai despejar backlog velho no Telegram** após o 1º batch (watermark avança sobre até 50 itens/dia de conteúdo antigo). **Decidir ANTES do 1º batch** (dono): avançar watermark manualmente / pausar digest durante o dreno / aceitar o despejo. Não pode ser surpresa.
- **E4 — Colisão dreno × entry das 09:00:** `items_without_distilled` é ORDER BY sem lock e `insert_distilled` não é idempotente — job agendado no meio de um batch pega os MESMOS itens = duplicatas reais. **Regra operacional: batch nunca na janela 09:00–09:35** (ou comentar a entry durante o dreno).
- **E5 — O teto do dreno é o GEMINI, não o LLM:** worker embedda 1 chamada/item → 3.2k requests ÷ ~1K RPD free ≈ **3–4 dias**. Verificar limites reais no AI Studio ANTES do 1º batch (tabela free agora é dinâmica). RPD do Gemini reseta **meia-noite do Pacífico** (≠ Groq UTC). Quota é por projeto: **em dia de dreno, `kubo query`/busca da UI podem tomar 429** — avisado. NÃO reescrever pipeline pra batch cross-item (mudança de worker/store por um one-off não se paga).

## Trilha A — código (dono-independente, TDD, PR-able)

| # | Entrega |
|---|---|
| A1 | **retry-after honrado** em `_call_with_backoff`: extrai header da exceção do LiteLLM, fallback pro backoff atual se ausente (normalização varia por provider — testar com respx). **Teto de espera 120s**: retry-after curto (janela de minuto) → espera e retenta; acima do teto (TPD/RPD) → desiste imediato. Só valores numéricos de header atravessam (§VIII), nunca corpo de resposta |
| A2 | **`RateLimitExhausted` ganha campo `scope` (`minute\|day\|unknown`)** → distiller mapeia pra `error.kind` `rate_limit_minute`/`rate_limit_day` (str livre, sem migração). `unknown` = header ausente/mentiroso, nunca crash |
| A3 | **Fix E2** (EmbeddingError → parcial gracioso, com teste) |
| A4 | Helper read-only `items_by_ids` na store (invariante 2 — serve auditoria E piloto) |
| A5 | Scripts one-off: `audit_sample.py` (gera o doc da auditoria), `distill_pilot.py` (template = `distiller_smoke.py`: `ApiExecutor.complete` + `filter_present_entities`, SEM run_worker, SEM persistência, SEM embedding), `drain_distill.py` (E1; batch 25–50, pacing, contadores) |
| A6 | ADR-0017 draft (D35, escopo one-off, fechamento com reconciliação) |

**NÃO fazer:** espalhar runs do distiller no schedules.yaml — com retry-after honrado o run único das 09:00 basta; múltiplos runs simultâneos = mesma raça da E4. Reavaliar só com evidência pós-retry-after.

## Trilha B — gates interativos do dono (intercalados com a A)

| Gate | Mecânica |
|---|---|
| B1 — Auditoria | Doc markdown lado a lado (item truncado no MESMO `input_char_cap`=20k que o LLM viu × summary), **gerado nos primeiros minutos** da sessão; dono julga enquanto a trilha A avança. Amostra **estratificada 8+4+4**: 8 distiller-recente (decide o dreno) + 4 legado PT + 4 legado EN (decidem re-destilação futura). Doc NÃO commitado (conteúdo coletado); só o agregado entra nas notas. **Rubrica fixada ANTES de entregar o doc:** por item — alucinação (binário, ELIMINATÓRIO) + fidelidade/PT-BR-natural/entidades (aprova·ressalva·reprova) + nota livre. Agregação POR ESTRATO: 1 alucinação = estrato reprovado; ≥80% aprova = ok. Julgar tendência, não caso isolado |
| B2 — Piloto | Mesmos 16 itens no candidato via LiteLLM (`openrouter/...` — só env key, zero mudança no executor), lado a lado com o llama. **Se a auditoria aprovar o llama, o candidato natural é o PRÓPRIO llama-3.3-70b via OpenRouter** (~US$0,10/M → dreno ≈ US$1,50–2) e o piloto vira A/A de formalidade. Relatório conta `malformed` por candidato (modelo com 10% malformado encarece o dreno em re-runs). Dono aponta o vencedor |
| B3 — Dreno (dias seguintes, fora da sessão) | Checklist pré-1º-batch: **spend limit na key do OpenRouter** configurado · limites do Gemini verificados no AI Studio · decisão E3 (digest) tomada · janela 09:00–09:35 respeitada. Batches supervisionados com "pode executar" por dia (ou pacote de dias, dono escolhe). Reconciliação final: N destilados novos, custo real, rejeitados motivados, delta de `items_without_distilled` → nota de fechamento no ADR-0017 |

## Regras de decisão (binárias, pré-fixadas)

- Auditoria reprovar o estrato do distiller por alucinação → **GO do dreno morre inteiro** até novo piloto aprovado (a conversa volta ao dono).
- Legados reprovados → **re-destilação fica FORA do dreno** (não por custo: não existe caminho de replace de distilled/chunks — ADR-0013 §VII; registra-se follow-up no ADR-0017 com pré-requisito explícito). Dreno é insert de coisa nova (caminho provado); replace é caminho inexistente.
- Limites reais do Gemini muito abaixo de ~1K RPD → reavaliar (dreno viraria semanas; aí batch cross-item ou Tier 1 do Gemini entram na conversa do dono).

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0017 antes de cravar.
2. **Extraordinária:** retry-after do Groq se revelar sistematicamente ausente/mentiroso; piloto reprovar TODOS os candidatos; limites do Gemini inviabilizarem o cronograma.
3. Conclusão da sessão (a conclusão do DRENO é a nota de reconciliação no ADR, dias depois).

## Tarefas do dono

- Julgar a auditoria (B1) e o piloto (B2) — rubrica pronta, ~30min do teu tempo.
- Configurar **spend limit** na key do OpenRouter (a sessão te guia) + `OPENROUTER_API_KEY` no `.env` (rito de sempre).
- Decisão E3 (digest durante o dreno) + "pode executar" por batch/dia.
- Antes de tudo: fechar a 0013 (relatório do smoke às ~21h + merge do #29).

## Ordem de sacrifício

1. **1º:** A2 (`scope` fino — fica `unknown` genérico se apertar).
2. **2º:** relatório de `malformed` por candidato no piloto (fica contagem simples).
3. **NUNCA cortáveis:** retry-after com teto (A1), fix EmbeddingError (A3 — pré-requisito de gastar dinheiro), auditoria com rubrica, piloto antes do dreno, spend limit antes do 1º batch, E4 (janela), ADR-0017.

## Critérios de aceite

- [ ] Run diário das 09:00 processa a fila inteira do dia no Groq free (retry-after provado com teste + observado em produção).
- [ ] `error.kind` discrimina `rate_limit_minute`/`rate_limit_day` (visível em Execuções).
- [ ] EmbeddingError no meio do lote persiste o parcial (teste).
- [ ] Auditoria julgada pelo dono com rubrica (agregado nas notas); piloto julgado; vencedor pinado no script por PR.
- [ ] Dreno: spend limit configurado antes do 1º batch; batches com run/proveniência; reconciliação final no ADR-0017 (N, custo, rejeitados, delta).
- [ ] Regime diário comprovadamente intocado (scheduler/`_DISTILLER_MODEL` sem diff).
- [ ] Cobertura ≥85%; ADR-0017 mergeado; PR conforme; main verificado.

## Escopo negativo da sessão

- Upgrade da conta Groq NÃO (D35 — free tier preservado). Mudança em `_DISTILLER_MODEL`/scheduler/schedules.yaml NÃO (exceto se o dono escolher pausar o digest na E3 — comentário reversível documentado).
- Re-destilar legados NÃO (follow-up com pré-requisito). Replace de distilled/chunks NÃO. Batch cross-item de embedding NÃO.
- Espalhar runs do distiller NÃO (reavaliar com evidência). Modelo por arg de CLI NÃO (constante pinada). Tier pago do Gemini NÃO (só se limites inviabilizarem — decisão do dono).
- Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-13; doc oficial Groq rate-limits verificada; decisões do dono D35–D36 (pagar pela velocidade; dúvida de qualidade motivou os 3 gates); consulta de validação ao advisor (Fable 5): GO com E1–E5 — dreno como script one-off sem tocar produção (pontualidade estrutural, não procedural), fix EmbeddingError antes de gastar, decisão do digest antes do 1º batch, janela anti-colisão 09:00–09:35, Gemini como teto real (~3-4 dias), retry-after com teto 120s + scope minute/day/unknown, auditoria 8+4+4 com rubrica eliminatória por alucinação, piloto via template do distiller_smoke sem persistência, candidato natural = llama-3.3 via OpenRouter (~US$1,50-2), re-destilação de legados fora (replace inexistente, ADR-0013 §VII).*
