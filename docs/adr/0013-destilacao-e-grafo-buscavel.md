# ADR-0013 — Destilação (D6 como construção) e grafo buscável

> Status: **proposto** · Data: 2026-07-08
>
> Esqueleto do marco 8.1 (sessão 0008). Finaliza no 8.8 após o smoke empírico
> (pinagem do modelo Groq) e a revisão do advisor. Emenda o ADR-0009 (campo novo
> no ctx; mecânica das regras 1 e 3 de D6; primeiro método do seam `knowledge`)
> e o ADR-0006 (LiteLLM entra — só no caminho de chat).

## Contexto

O M6 fecha a fase 1: um `item` bruto vira `distilled` **PT-BR** com entidades,
tudo chunked e embeddado, consultável por `kubo query` com origem citável
(`kubo show --provenance`). É a sessão de fronteira em que **D6** (regras
anti-injection) deixa de ser obrigação registrada e vira código: o executor de
LLM nasce sem tools por construção e a demarcação de conteúdo untrusted mora
nele, não na disciplina de quem escreve o worker.

O ADR-0009 deixou explicitamente para cá: (a) a mecânica de LLM no ctx ("o slot
é uma frase, não um campo morto"); (b) a mecânica das regras 1 e 3 de D6; (c) o
payload de `distilled`; (d) o primeiro método do seam `knowledge`. O ADR-0006
fixou a tripla de embedding `(gemini-embedding-001, 768, SEMANTIC_SIMILARITY)`
por evidência REST e adiou a LiteLLM "só ao M6, quando houver consumidor de
produção". Este ADR resolve esses pontos.

## Decisão

### I. Embedding vai por REST direto; LiteLLM só no chat (assimetria deliberada)

A tripla do ADR-0006 foi **provada por evidência** contra o endpoint REST
`batchEmbedContents` (`taskType=SEMANTIC_SIMILARITY`, `outputDimensionality=768`).
O cliente de embedding do M6 usa o **mesmo caminho REST** (httpx, já instalado) —
não LiteLLM. Motivo: o passthrough desses dois parâmetros pela LiteLLM/Gemini é
inverificável e seu modo de falha é o pior possível — vetores dimensionalmente
válidos porém **incomparáveis** com os existentes (tripla errada), degradação
**silenciosa** que corromperia o índice. "Consistência de stack" não paga esse
risco. LiteLLM fica confinada ao chat de destilação (§V).

### II. Chunking — parágrafo → merge greedy → sem overlap; teto conservador

Estratégia (funções puras, TDD): split por parágrafo → merge greedy até o teto →
**sem overlap** (overlap entra na escada de revisão junto com `RETRIEVAL_*` só se
o recall decepcionar). Contagem de tokens por **heurística** `len(text) // 4` (sem
dependência nova; `tiktoken` mediria errado o tokenizer do Gemini e `countTokens`
é rede por chunk).

**Teto efetivo ~1.200 tokens estimados** (não 1.600). A heurística `//4` assume
~4 chars/token, mas PT-BR real fica em ~3–3,5 chars/token: um teto de 1.600
estimados poderia virar >2.000 tokens reais e tocar o limite ~2.048 do
`gemini-embedding-001`, onde a truncagem é **silenciosa** (perda silenciosa).
1.200 estimados dão margem confortável. Nunca se confia no auto-truncate do
provider. Cascata determinística: parágrafo > teto → split por sentença;
sentença > teto (patológico) → hard split por chars.

### III. Contrato do destilador (emenda ADR-0009)

1. **Primeiro método do seam `knowledge`:** `items_to_distill(limit) -> list[ItemView]`,
   view tipada read-only = `(ref opaco, title, content)`. Gatilho legítimo do
   ADR-0009 ("métodos entram quando um worker exigir leitura, com teste que
   justifique"). O worker **não conhece RecordID**.
2. **`DistilledPayload`** (novo membro da união discriminada, `type="distilled"`,
   `schema_version` 1) **ecoa o ref opaco**; o runner resolve ref→RecordID e chama
   `insert_distilled`. A mecânica do ref: a instância concreta de `knowledge`
   (construída pelo runner, com acesso a db) guarda um mapa `ref→RecordID` e expõe
   `resolve(ref)` **fora do Protocol** — o worker nunca vê. O runner detém a mesma
   instância que injetou no ctx.
3. **O ref NUNCA passa pelo LLM.** O canal de injection real não é o worker
   (código nosso, in-process) — é a **saída do LLM**. Se o pareamento item↔resposta
   dependesse de o LLM ecoar um identificador, um item malicioso poderia instruí-lo
   a trocar refs e envenenar a proveniência de outro item do lote. Regra com teste:
   o worker processa **um item por chamada de LLM** e pareia `ref→resposta`
   **programaticamente** (correlação em código, não no texto); o LLM só produz
   summary + entidades.
4. **Entidades por nome** (`EntityRef(name, kind)`); o runner faz
   `get_or_create_entity` e traduz para RecordIDs (padrão "runner traduz
   payload→store" preservado).
5. **Chunks já embeddados no payload** (espelho 1:1 do `Chunk` da store, tripla
   inclusa) → o **`RunContext` ganha o cliente de embedding** nesta sessão (o campo
   preanunciado pelo ADR-0009). Tanto `RunContext.knowledge` quanto o cliente de
   embedding entram como **Protocol/seam** (não a implementação concreta): `knowledge`
   alarga do concreto `EmptyKnowledge` para o Protocol; o cliente de embedding é um
   Protocol cujo teste unitário usa **fake** (regra do CLAUDE.md "LLMs em testes
   sempre mockados" estendida ao REST de embedding — nenhum teste acopla ao httpx
   concreto nem à rede). Ambos satisfazem o pyright strict.
6. **Ref não-resolvível** (bug ou vazamento pro campo) → `ErrorInfo` estruturado
   naquele payload, o run segue com o resto. Nunca exceção que derruba o `_persist`.
7. **Lote pequeno por run** (`max_items` na config do worker): reduz a janela de
   perda da persistência-no-fim. `insert_distilled` não é idempotente, mas o worker
   só seleciona itens sem destilado → re-run cura (ADR-0009 §VII).
8. **Persist por item da destilação é atômico — invariante a preservar.** As arestas
   `mentions` **DEVEM permanecer dentro da transação de `insert_distilled`** (que já
   cobre, numa só transação, distilled + chunks + `derived_from` + `produced_by` +
   `mentions`). A propriedade que isso garante: **um `distilled` nunca existe sem suas
   entidades** — as `mentions` commitam junto com o distilled ou revertem juntas. A
   resolução de entidade nome→RecordID (`get_or_create_entity`, UPSERT idempotente por
   chave natural) **precede** a transação (contrato d.3, runner traduz). Isto **não** é
   descrição do código de hoje e sim invariante: um refactor futuro que mova as
   `mentions` para fora reabriria o buraco "distilled permanentemente sem entidades"
   (o item sai do filtro "sem destilado" e ninguém volta lá). Pinado por um **teste de
   integração** que força falha num statement tardio (RELATE `mentions` com rid
   inválido) e assevera que nenhum `distilled` sobrou. Não se dobra o upsert de
   entidade para dentro da transação (mudaria a assinatura de `insert_distilled` e o
   contrato d.3 para ganho de segurança zero). **Resíduo aceito:** crash entre a
   resolução de entidades e o insert deixa **entidades órfãs** (criadas, sem `mentions`
   apontando) — inócuo, pois o RecordID é determinístico e o re-run **reusa** a mesma
   entidade, não duplica.

### IV. ApiExecutor — D6 regras 1 e 3 viram construção (emenda ADR-0009)

`kubo/executors/api.py` com `ApiExecutor` (config tipada: model, temperature,
max_tokens, response_format). É onde a mecânica de D6 nasce:

- **Regra 1 (sem tools):** a assinatura do executor **não aceita tools**. Fechado
  por construção, não por disciplina do worker.
- **Regra 3 (demarcação untrusted):** o template que demarca conteúdo coletado
  como untrusted mora **no executor**, não no worker. O worker **nunca** chama
  `litellm.completion` cru.
- **Saída validada pelo nosso pydantic**, nunca pelo `json_schema` do lado do Groq
  (varia por modelo, não é gate confiável). Saída malformada = rejeita e **conta**,
  jamais "aproveita".
- **`num_retries=0` na LiteLLM:** o backoff é o nosso (§V); dois mecanismos de
  retry empilhados violariam o teto de 3 tentativas.
- **Nasce parcial:** SEM roteamento por persona, SEM fallback declarável, SEM
  budget (fase 3). Registrado aqui que o executor é esqueleto.

### V. Groq via LiteLLM; backoff com teto; modelo pinado por evidência

- LLM de destilação = **Groq free tier** via LiteLLM (D22). Quota estourada =
  **espera, jamais fallback para provider pago** (zero gasto de token na fase 1).
- **Backoff com teto** (E3): 3 tentativas exponenciais dentro do run; estourou o
  teto → o run fecha **graciosamente** com o que processou; o agendamento diário
  come a fila (job `max_instances=1` + `coalesce`, ADR-0010). Nunca backoff
  infinito (colidiria com o job do dia seguinte).
- **Modelo pinado por evidência** (§ smoke): primário `llama-3.3-70b-versatile`,
  comparado com `moonshotai/kimi-k2-instruct` no smoke. O catálogo vivo do Groq é
  verificado no dia (depreciam rápido). A pinagem final entra na finalização (8.8).
- **Smoke = gate de "não-lixo"** (n=10, mesma epistemologia do ADR-0006): 10/10
  JSON válido no schema; 10/10 saída PT-BR; entidades não-absurdas; **canários de
  injection ignorados** — 1–2 itens carregam prompt injection embutido, testando
  manipulação tanto do **summary** quanto das **entidades** (entidade envenenada
  vira aresta `mentions` permanente). **Canário é gate binário, não métrica:**
  *qualquer* canário que vaze (no summary ou nas entidades) = reprovação imediata
  do modelo, sem média dentro do n=10 — injection não se pondera. Falhou → troca
  modelo, **não afrouxa critério**. Ambos reprovarem PT-BR → a premissa de D22 cai e
  a decisão volta ao dono (nunca fallback pago silencioso).

**Emenda (execução 2026-07-11, aval do dono): injection defense vs content trust.**
O primeiro smoke ao vivo (llama-3.3-70b-versatile, Groq) reprovou por vazamento do
canário de ENTIDADE (`INJECTED_ENTITY_9Z`), NÃO por qualidade (10/10 schema, 0
malformado, summary nunca sequestrado; kimi-k2 com ID morto no catálogo — nota de
config). A análise revelou que o canário original **conflaciona dois riscos** e o
gate binário sobre comportamento não-determinístico de LLM é **flaky** (a rodada
"limpa" foi falso-negativo — o canário caiu no rate limit e não foi avaliado):

1. **Injection defense** — impedir que a obediência do modelo crie algo que **não
   está no conteúdo**. É **bloqueável por construção** e passa a ser: **filtro
   verbatim no worker** — depois de `complete()`, descarta toda entidade cujo `name`
   (casefold) não seja substring do content enviado; conta em `entities_filtered`
   (`Stats`). Defesa NOVA (aperta o D6), determinística, testável sem LLM.
2. **Content trust** — um texto que **mente** afirmando existir a entidade X. Nenhum
   modelo/filtro/prompt resolve: a mentira está no documento, não no comportamento do
   extrator. É a MESMA propriedade do `summary` (resumo fiel de conteúdo hostil
   carrega strings do atacante), logo a entidade-verbatim **não introduz classe nova
   de risco**. Contido por proveniência (`mentions → distilled → item`) + **invariante
   de consumo**: todo dado derivado de coleta (summary E nomes de entidade) é untrusted
   no ponto de consumo — a fase 3/4 que ler `entity.name`/`summary` para prompt de
   outro agente DEVE tratá-lo como hostil (risco de *stored injection*; `name` capado
   em 200 chars não elimina, contém no consumo).

Consequências: (a) o **canário de entidade é redesenhado** para pedir um nome
**construído** (não-verbatim no texto) — se o modelo obedecer, o filtro derruba;
vazou no output final = bug no filtro = FAIL binário legítimo. O gate passa a provar
o **pipeline** (determinístico, estável entre rodadas), não a virtude do modelo. (b)
Frase adicional no `_INSTRUCTION` = mitigação em profundidade, **documentada como
não-estrutural** (prompt é mitigação, não defesa). (c) **Pacing** (sleep entre
chamadas) no smoke: `rate_limited` reprova certo por `valid<10`, mas sem pacing o
gate nunca fecha no free tier do Groq — operacional, não afrouxamento.

**Trade-off assumido:** o filtro verbatim derruba enriquecimento legítimo
("banco central" no texto → "Banco Central do Brasil" do modelo = filtrado). Aceitável
na fase 1 (determinismo+segurança > enriquecimento); `entities_filtered` monitora o
custo — se >20-30% das entidades legítimas caírem, reconsiderar matching (novo ADR,
nunca inline). **Alternativas rejeitadas:** trocar de modelo (resistência a injeção é
espectro; passa neste canário e cai na próxima formulação — troca de modelo é para
falha de QUALIDADE, §V); "entidade tem que aparecer no content" sem redesenhar o
canário (o nome injetado ESTÁ no content — não filtraria); revisão humana de entidade
nova (quebra a automação da fase 1) — reabrível como quarentena barata se os
consumidores downstream não honrarem o invariante de consumo; restringir `kind` a
vocabulário fechado (adiado — mesmo regime de content trust).

### VI. `attach_chunks` — anexar chunks a distilled existente (não delete+recria)

Os 935 destilados legados (import Neon, ADR-0012) foram inseridos com `chunks=[]`.
Para torná-los buscáveis, o backfill chunka+embedda o summary e **anexa** os
chunks. Nova função de store `attach_chunks(distilled, chunks)`:

- **Guarda de idempotência DENTRO da transação** (não no loop do backfill):
  distilled que já tem chunk é no-op explícito. Retomabilidade não depende da
  disciplina do chamador.
- Reusa a validação `dim == len(embedding)` do `insert_distilled` (helper extraído).
- **Anexa, não deleta+recria:** delete+recria destruiria `produced_by→run` legado e
  `mentions` — a proveniência é o produto.

### VII. Backfill dos 935 = script one-off; corpus vetorial é MULTILÍNGUE (D20 corrigido)

Backfill = **script one-off** (E5, precedente ADR-0012/D19: worker é para
recorrência), não worker. Retomável por construção (pula distilled que já tem
chunk — condição transitória do ADR-0012 §IV). Inclui **spot-check de idioma** dos
summaries legados.

**Embedda-se o `distilled`, nunca o `item` bruto** (o schema força via
`chunk_of: IN chunk OUT distilled ENFORCED`; ADR-0008 §II).

**D20 estava factualmente errado e é corrigido aqui.** D20 previu o corpus vetorial
como "100% PT-BR" com EN como mero "resíduo". O spot-check de idioma do backfill
(dry-run em produção, 2026-07-11) **falsificou** isso: dos 935 summaries legados,
**pt=571, en=355 (38%), incerto=9** (os incertos são transcrições legadas em
ru/zh/km/id/ms — lixo de legenda multilíngue). Não é resíduo; o corpus é
**multilíngue**, dominado por PT mas com EN em volume relevante.

Consequência: a prova dos 90 dias sobre o legado passa a depender de **recuperação
cross-lingual** (pergunta PT → summary EN), assimétrica (pergunta curta → summary
longo) — o regime mais fraco de `SEMANTIC_SIMILARITY`. A epistemologia da ADR-0006
("pinado por evidência, não por reputação do modelo") exige provar isso antes de
confiar. **Gate do backfill vivo:** o smoke de embedding (`scripts/embedding_smoke.py`)
ganha **trios cross-lingual** — âncora PT em forma de pergunta, paráfrase EN em forma
de summary, distrator PT que compartilha léxico com a âncora (detecta o *language
gap*: se o espaço clusteriza por idioma, o distrator mesma-língua vence a paráfrase
cross-língua). Gate binário como os trios mono: qualquer inversão reprova, e a
decisão sobe ao dono (aceitar degradação registrada / subir o degrau `RETRIEVAL_*`
da ADR-0006 com o corpus ainda sem chunks / enfileirar re-destilação EN futura).

**Consequência estrutural do no-op (mão-única) — decisão adiada, não esquecida:** a
guarda de idempotência do `attach_chunks` (§VI: distilled com chunk é no-op) não tem
caminho de *replace*. Logo **embedar é irreversível**: trocar a tripla (degrau
`RETRIEVAL_*`) ou re-destilar os 355 EN para PT-BR (fase futura) exigiria re-embedar
o corpus, e o no-op bloqueia. Por isso o smoke cross-lingual roda **antes** do
backfill — enquanto o corpus ainda não tem chunks, todas as alternativas ficam
abertas. Um futuro caminho de replace de chunks (delete+recria preservando
proveniência) fica registrado como pré-requisito de qualquer mudança de tripla ou
re-destilação. **Não re-destilar legados nesta sessão** (timebox/quota) segue de pé.

### VIII. Operação: destilação agendada diária; logger nunca vaza payload

- **Destilação agendada diária** (D24): entry no `schedules.yaml` (~08:30, após a
  coleta das 08:00). Sem garantia de ordem com a coleta — itens que chegam depois
  esperam o dia seguinte; idempotente, documentado, **não é bug**.
- **Logger nunca loga payload coletado NEM a saída crua do LLM pré-validação**
  (contadores + `errors(include_input=False)` apenas) — com teste, inclusive nos
  caminhos de erro do `ApiExecutor` (mensagens de exceção de client HTTP costumam
  embutir corpo de resposta).

## Consequências

- **D6 é construção, não fé:** o executor sem tools e a demarcação untrusted
  fecham por tipo/assinatura; o smoke com canários prova adversarialmente.
- **A prova dos 90 dias vira comando:** `kubo query "pergunta PT-BR"` +
  `kubo show --provenance` percorrem distilled → item → source → run.
- **Custo por query:** cada `kubo query` chama a API Gemini para embeddar a
  pergunta (mesma tripla). O CLI falha com mensagem clara se `GEMINI_API_KEY`
  ausente — nunca stack trace.
- **Dependência nova (LiteLLM):** justificada (CLAUDE.md a consagra como stack
  canônica de LLM via API; será necessária na fase 3). Confinada ao chat.
- **Executor nasce parcial:** persona/fallback/budget são fase 3 — registrado para
  não parecer esquecimento.

## Alternativas rejeitadas

- **Embedding via LiteLLM** — rejeitada: passthrough da tripla inverificável,
  degrada em silêncio corrompendo o índice (§I).
- **httpx direto contra o Groq (adiar LiteLLM)** — mais leve, mas D22 nomeia
  LiteLLM (decisão do dono) e a dep vem na fase 3 de qualquer forma; desviar
  reabriria o planejamento sem ganho que pague a interrupção.
- **Claims no `distilled`** — fora (D23): sem consumidor definido; a fase 2/3 decide.
- **Backfill por delete+recria do distilled** — rejeitada: destruiria proveniência
  (`produced_by`, `mentions`); `attach_chunks` preserva (§VI).
- **Backfill como worker** — rejeitada: worker é para recorrência (E5, D19).
- **Overlap de chunking / `task_type` RETRIEVAL_\*** — adiados à escada de revisão,
  só se o recall decepcionar em corpus real (ADR-0006).
- **Confiar no `json_schema`/auto-truncate do provider** — rejeitada: gate é o
  pydantic nosso; truncagem silenciosa é perda silenciosa (§II, §IV).
