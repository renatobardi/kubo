# ADR-0006 — Embeddings: modelo, dimensão, métrica e task_type

> Status: **aceito** · Data: 2026-07-05

## Contexto

O ADR-0005 (GO do spike SurrealDB) deixou **diferida** a decisão de embeddings — modelo, dimensão, métrica — condicionando o M3 a ela: "nenhuma migration de índice HNSW de conhecimento nasce de palpite antes" deste ADR. O smoke ao vivo foi cortado no M2 por ausência de `GEMINI_API_KEY`; esta é a mini-sessão dedicada que o resolve (plano 0002 §2.1.6).

O Kubo embedda **conteúdo coletado em português** — artigos RSS, HTML, transcrições — para curadoria de conhecimento em grafo e busca vetorial (KNN HNSW no SurrealDB). O contrato de busca da store já foi fixado no ADR-0005: uma única função KNN que sempre injeta EF; a métrica ficou para cá.

**Evidência (smoke ao vivo, `scripts/embedding_smoke.py`, 10 trios PT-BR).** Cada trio é (âncora, paráfrase com outras palavras, distrator). Os casos 6–10 usam **polissemia adversarial** — o distrator compartilha a palavra exata com a âncora em outro sentido (banco instituição/assento, fonte RSS/tipográfica, prova evidência/exame, luz energia/lâmpada, remédio medicamento/solução). Casamento lexical falharia; embedding semântico são ordena sim(âncora,paráfrase) > sim(âncora,distrator). Métrica: cosseno, via API Gemini `batchEmbedContents` com `task_type=SEMANTIC_SIMILARITY`.

| Config | Ordenação | Margem mínima |
|---|---|---|
| `gemini-embedding-001` @ 768 | **10/10** | +0.0624 |
| `gemini-embedding-001` @ 3072 | 10/10 | +0.0571 (≈ idêntico ao 768) |
| `gemini-embedding-2` @ 768 | 10/10 | +0.1267 (separa ~2×) |

Dois fatos decidem: (1) a truncagem MRL para 768 **não perde qualidade** vs 3072 nesta tarefa; (2) ambos os modelos acertam todos os distratores de polissemia.

## Decisão

O embedding do Kubo é a **tripla `(modelo, dimensão, task_type)`** — vetores gerados com triplas diferentes não são comparáveis entre si.

- **Modelo: `gemini-embedding-001`** (text-only, GA).
- **Dimensão: 768** (truncagem MRL).
- **`task_type`: `SEMANTIC_SIMILARITY`** — valor único para geração **e** busca (uso simétrico). É a escolha fadiga-de-complexidade: um só valor, e o único validado pelo smoke.
- **Métrica: cosseno** — `DEFINE INDEX ... HNSW ... DIST COSINE`, coerente com `SEMANTIC_SIMILARITY` e com a função KNN única do ADR-0005.

Escolho o `001` sobre o `gemini-embedding-2` **por contrato, não por qualidade**: `task_type` é um parâmetro enumerado, verificável e estável; o `embedding-2` expressa a tarefa como instrução em linguagem natural no prompt — superfície fuzzy que degrada em silêncio, o que um mantenedor solo não quer depurar em 6 meses. Com n=10 e ambos a 10/10, o smoke tem poder para distinguir "funcional" de "quebrado", não para distinguir qualidade — logo a separação 2× melhor do `embedding-2` não é critério de decisão. 768 sobre 3072 porque o smoke prova paridade e 768 custa **4× menos** RAM/disco/índice HNSW na instância OCI (liga direto ao risco "restart ≠ persistência do índice / RAM no boot" do ADR-0005).

## Consequências

- **Proveniência obrigatória:** todo registro embeddado carrega `(modelo, dim, task_type)` como metadado (a migration de schema do M3 fixa isso). É o que torna o custo de re-embed um mecanismo mecânico e detectável, não arqueologia, no dia em que o modelo mudar.
- **Corpus fonte sempre preservado:** o texto original nunca é descartado em favor do vetor — pré-condição de qualquer re-embed.
- **Chunking é obrigatório no M3:** o limite de input do modelo (~2k tokens) faz da unidade de embedding o **chunk**, não o documento — artigos e transcrições excedem o limite. A estratégia de chunking fica diferida ao M3, mas nasce constrangida por esse limite (não é opcional).
- **Escopo da evidência:** o smoke valida **sanidade semântica em PT-BR sob polissemia** (n=10, textos curtos, autorais). **Não** valida recall em escala de corpus nem qualidade de chunking. Nenhuma constante de similaridade/corte na store pode derivar dos números do smoke — calibração, se preciso, vem de dados reais.
- **Custo de re-embed aceito:** trocar qualquer elemento da tripla exige re-embeddar o corpus inteiro. Em escala pessoal, horas (key gratuita). Aceito.
- **Escada de fallback (ordem de custo):** 768 → 1536 (mesmo modelo, só MRL + re-embed) → `gemini-embedding-2` (muda o contrato). Barateia a decisão futura e evita re-litígio.
- **litellm não entra agora** — só no M6, quando houver consumidor de produção (escopo negativo do plano 0002). O smoke é stdlib pura.

**Riscos monitorados (nomeados, não eliminados):**
- **Deprecação pelo Google:** modelos de embedding do Google têm ciclo curto (histórico: gecko, `text-embedding-004`). Mitigação = proveniência + corpus fonte preservado (re-embed é operação mecânica).
- **Dependência dura de provider externo** no caminho de conhecimento — nova no projeto. Reversível justamente pela proveniência.
- **Tier gratuito do AI Studio:** (a) rate limits — irrelevantes em escala pessoal, só alongam o re-embed; (b) **ToS: o Google pode usar o conteúdo enviado para melhorar produtos.** Aceitável para RSS/HTML público; se notas pessoais entrarem no corpus, a resposta é upgrade para tier pago (zero mudança de código).
- **Margens de textos curtos:** o corpus real (documentos longos, chunking, ruído de HTML) pode comprimir as margens antes do esperado — a escada de fallback é o seguro.

## Alternativas rejeitadas

- **`gemini-embedding-2`** — separa ~2× melhor nos trios, mas contrato task-as-instruction (fuzzy, degrada em silêncio) e multimodal que não usamos; n=10 não dá poder para provar qualidade superior real. Fica na escada de fallback, não descartado.
- **Dimensão 3072 (ou 1536)** — 3072 não deu ganho vs 768 no smoke a 4× o custo. 1536 fica na escada, revisável se recall de busca decepcionar em corpus real.
- **`task_type=RETRIEVAL_DOCUMENT`/`RETRIEVAL_QUERY`** — pareamento canônico do Gemini para busca query→documento, mas exige dois valores assimétricos e não foi validado pelo smoke. `SEMANTIC_SIMILARITY` simétrico é a escolha de menor complexidade; revisável se o recall de busca decepcionar.
- **Modelo self-hosted** (multilingual-e5, bge-m3 etc.) — carga de ops + RAM na instância OCI + qualidade em PT-BR sem avaliação própria; contra a fadiga de complexidade. Reconsiderável só se o `001` for deprecado sem sucessor de contrato explícito.
