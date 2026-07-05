# ADR-0008 — Desvios do schema de conhecimento (§2.3)

> Status: **aceito** · Data: 2026-07-05

## Contexto

A spec funcional (§2.3) define o schema de conhecimento como quatro tabelas (`source`, `item`, `distilled`, `entity`) com **"Embeddings como campos vector nos records `distilled` e `memory` (índice HNSW)"**. A implementação da migration `kubo/store/migrations/0001_knowledge_schema.surql` diverge dessa declaração em três pontos estruturais e um temporal, todos constrangidos por decisões técnicas a jusante (ADR-0006 sobre chunking; ADR-0002 sobre `run`) e pela fase 1.

Esta decisão (ADR-0008) **registra os desvios, a justificativa técnica de cada um e as reversões previstas** quando as precondições mudarem (fase 3).

## Decisão

### I. Chunk-como-registro (desvio permanente, CONTRARIA a spec literal)

**Desvio:** a spec coloca embedding em `distilled`; a implementação cria tabela `chunk` com o vetor, ligada a `distilled` por aresta `chunk_of: chunk -> distilled`.

**Justificativa técnica (o fundamento durável):**
1. **Granularidade de recuperação.** Buscar em grão fino (chunk) e devolver em grão grosso (distilled) é superior a um-vetor-por-documento **independentemente do limite de input do modelo**: um documento longo tem múltiplos pontos semânticos, e um único vetor-média dilui todos eles. Este é o argumento que torna o desvio **permanente** — não depende de nenhum parâmetro contingente.
2. HNSW indexa exatamente **um vetor de dimensão fixa por registro SurrealDB**. Um campo `embedding` em `distilled` significaria ou (a) "só o primeiro chunk conta" — perde semântica do documento; ou (b) um array-de-vetores que o índice rejeita — incompatível com HNSW.
3. Logo: **o embedding mora obrigatoriamente no chunk** — um chunk sem vetor é erro de construção, não estado válido.

**Constrangimento adicional da fase 1 (não é o fundamento):** o ADR-0006 fixa o modelo (gemini-embedding-001 @ 768) com ~2k tokens de entrada máxima, e o corpus real (RSS/HTML, transcrições em PT-BR) ultrapassa isso regularmente — então chunking já seria obrigatório mesmo que a granularidade não o exigisse. Um modelo futuro de janela larga removeria este constrangimento **sem** invalidar o desvio, porque o fundamento é a granularidade (ponto 1), não o limite de token.

**Proveniência e busca:** KNN roda sobre `chunk` (o índice HNSW resolve os 768-d por record); a busca devolve `chunk` e a store resolve `chunk -[chunk_of]-> distilled` para entregar conhecimento (o destilado), não o fragmento órfão. Padrão comum em RAG, não invenção.

**Fase 3 adiante:** quando `memory` entrar (tabela nova, com próprios produtores/consumidores), o mesmo padrão chunk-como-registro aplica por simetria. **Não há reversão prevista: o desvio é permanente** (fundado na granularidade, ponto 1) e a spec deve ser emendada na revisão de arquitetura da fase 3 — este ADR é o registro do conflito que o CLAUDE.md exige apontar ao dono.

### II. Item preservado, não embeddado

**Desvio:** `item` existe (corpus fonte bruto) mas não recebe campo `embedding`. Só `chunk` (derivado de `distilled`) é vetorizado.

**Justificativa:**
1. O produtor do embedding é o worker que **destila** (resumo → distilled → chunk). Embeddar o bruto (`item.content`) seria responsabilidade de worker **diferente** — um pré-destilador — que não existe na fase 1.
2. O corpus bruto é preservado em `item.content` (obrigação de ADR-0006, "corpus fonte sempre preservado"), mas vetorização é especialização posterior — fora do escopo da fase 1.

**Fase 3 adiante:** um worker novo de pré-destilação ou indexação pode criar chunks diretamente de `item` (sem passar por `distilled`), com seu próprio embedding. A relação `chunk_of` (migration 0001) define `OUT distilled` com enforcement — aceitar `item` como pai exige redefinir a relação por migration (uma linha: `OUT distilled|item`), mudança estrutural do schema.

### III. Memory e grounded_in adiados (D17 — decisão de fase)

**Desvio:** a spec (§2.3, "`memory` (4 categorias — episódica, semântica, procedural, working)") inclui a tabela `memory` e a aresta `memory -[grounded_in]-> distilled|item`. A migration 0001 não cria estas tabelas.

**Justificativa:**
- **Sem produtor nem consumidor (D17):** nenhum worker na fase 1 escreve `memory`; a persona que consumiria (contexto de agente) chega na fase 3. Criar schema sem consumidor é especulação — esta é a razão suficiente do adiamento.
- Nota de orçamento (não é a razão do adiamento): `memory` está **na spec**, então a contenção de tabelas *extra-spec* da ADR-0002 (cláusula "Contenção explícita") não se aplica a ela. Vale só registrar o efeito colateral: com `run` (1ª extra-spec) e `chunk` (2ª), o orçamento de duas tabelas fora da spec está esgotado — uma terceira extra-spec exigiria reabrir planejamento.

**Fase 3 adiante:** quando persona/agente chegar a consumir contexto histórico, `memory` entra por migration — e aplicará o **mesmo padrão chunk-como-registro** do item I (memory tem seus próprios chunks se exceder o limite de token).

### IV. Produced_by aponta para run, não flow (ADR-0002)

**Desvio:** a spec define `distilled -[produced_by]-> flow` (§2.3, arestas cross-schema). A migration define `produced_by: distilled -> run` (temporário).

**Justificativa e reversão:** coberto completamente por ADR-0002. Apontar para `run` na fase 1 (não existe `flow` ainda); migration da fase 3 restaura o alvo spec (`produced_by -> flow`).

### V. relates_to.kind: string livre em vez de tipada (desvio menor)

**Desvio:** a spec define a aresta `entity -[relates_to]-> entity` **tipada** ("uses, competes, part_of..."). A migration 0001 implementa `relates_to.kind` como `string` livre, sem enum nem `ASSERT`.

**Justificativa:** nenhum produtor na fase 1 escreve `relates_to` (a extração de relações entre entidades é da destilação, M6). Cravar o vocabulário fechado agora — sem um consumidor que o exercite — seria adivinhar o conjunto de tipos. String livre não bloqueia nada e o gate humano da destilação define o vocabulário quando ele existir.

**Gatilho de reversão:** quando houver produtor de `relates_to`, tipar o `kind` (enum/`ASSERT`) por migration, com o vocabulário derivado do uso real. O comentário na migration 0001 aponta este ADR.

### VI. Aresta `collected_by`: proveniência de execução item→run (emenda, sessão 0005)

**Desvio:** a migration 0003 (sessão 0005) introduz a aresta `item -[collected_by]-> run`, registrando qual execução coletou cada item.

**Justificativa (o argumento central):** `collected_by` é o **análogo item-side da aresta `produced_by` que o próprio ADR-0002 criou**. Juntas, elas completam a cadeia de proveniência que a "prova dos 90 dias" exige — a missão da sessão 0005 declarava "proveniência completa (item→source E item→run)". A aresta `produced_by` rastreia `distilled -> run` (quem destilou); `collected_by` rastreia `item -> run` (quem coletou). Ambos os endpoints (`item`, `run`) já existem; zero nova superfície de tabela. O padrão é precedente em ADR-0008 §I (`chunk_of` é uma aresta de proveniência que entrou sem consumir o orçamento de tabelas extra-spec), com a diferença que `collected_by` nem sequer contraria a spec — apenas a estende onde a spec deixou silêncio (arestas provenance não são enumeradas em §2.3, só as tabelas).

**Permanência explícita e contraste com `produced_by`:** `collected_by` é **PERMANENTE** e mantém apontando para `run` mesmo na fase 3. Fundamento: coleta é um fato de **EXECUÇÃO** — mesmo quando `flow` entrar (fase 3), runs continuarão coletando itens periodicamente. Destilação é que se torna produto de `flow` (por isso `produced_by` reaponta para `produced_by: distilled -> flow` na fase 3, per ADR-0008 §IV). Coleta nunca reaponta; um `run` coletou o item permanentemente. Esta assimetria (coleta permanente, destilação refatorada) é produto da diferença semântica: `run` é operacional (quando algo ocorre), `flow` é de design (o padrão armazenado). Só `produced_by` mudará.

**Contenção e clarificação do orçamento (ADR-0002):** ADR-0002 limita **TABELAS** extra-spec ("uma terceira tabela extra-spec é sinal de scope creep"). ADR-0008 §III nota que o orçamento de 2 tabelas extras (run + chunk) está esgotado — uma **terceira** tabela reabre planejamento. `collected_by` **NÃO consome este orçamento**: é uma aresta (não tabela), ambos endpoints já existem e são do spec (`item`) ou da extensão aceita (`run`), e segue o precedente de aresta de proveniência (§I: "um chunk sem vetor é erro de construção"; uma aresta de proveniência é o mecanismo de registrar esse fato). Explicitamente: uma aresta extra-spec entra apenas quando (a) ambos endpoints já existem E (b) serve proveniência/observabilidade — qualquer outra finalidade reabre a contenção.

**Semântica de re-coleta: last-write-wins.** A aresta é reescrita (DELETE+RELATE) na mesma transação que o upsert do item, consistente com a semântica de `from_source` do worker de coleta. Portanto a aresta registra o **ÚLTIMO run coletador**; o histórico **COMPLETO** de coleta mora na tabela `run` (não leia a aresta como auditoria — isso se leria como bug de projeto). O worker idempotente re-coletando o mesmo `external_id` sobrescreve a aresta e também sobrescreve o item (ambos por upsert determinístico de chave natural); o grafo fica íntegro.

---

## Detalhes técnicos menores (consequências do design principal)

### Nomes e tipos de campo

- **`seq` em vez de `order`:** campo de sequência no chunk. `order` é palavra reservada em SurrealDB; padroniza a colisão como `seq` para evitar surpresas em toda query/shell.
- **Proveniência de embedding (3 campos planos):** `model`, `dim`, `task_type` no chunk (redundância tolerada). O `dim` é mantido apesar de poder ser derivado do tipo `array<float, 768>` — no re-embed futuro (escada do ADR-0006: 768 → 1536 → `embedding-2`), é o `dim` por record que detecta estado misto durante migração (alguns chunks em 768, outros já em 1536).
- **Tipo de tamanho fixo `array<float, 768>`:** enforcement do banco — vetor de dimensão errada é rejeitado, não silenciosamente arredondado (validado empiricamente contra v3.1.5). Re-embed de 768→1536 exige migração do TIPO do campo (DDL + dados), não só atualização de valor — é feature, não limitação.

### Índice HNSW

- **Parâmetros `EFC 150 / M 12`:** iguais aos defaults do SurrealDB v3.1.5 (pinado em ADR-0005). Padronizados de propósito — um bump de server que mude defaults não altera o índice em silêncio.
- **Métrica: cosseno** — `DEFINE INDEX ... DIST COSINE`, coerente com `task_type=SEMANTIC_SIMILARITY` do ADR-0006 e com a função KNN única da store (ADR-0005).

### Integridade estrutural

- **Arestas `ENFORCED`:** proveniência é o produto — uma aresta para endpoint inexistente quebraria a "prova dos 90 dias" (prova de que cada destilado tem procedência rastreável). O banco garante que os dois pontos existem, validado em transação (ADR-0007).
- **Postura `SCHEMAFULL` em tabulações de conhecimento:** entrada coletada é hostil por padrão (prompt injection via conteúdo coletado é ameaça de primeira classe do projeto). O schema é a primeira borda de validação. `FLEXIBLE` só em payload variável (`item.metadata`, `run.stats`, `run.error`).
- **Índices `UNIQUE` em `source.canonical` e `entity.normalized`:** rede de segurança. Os record IDs são derivados do canonical/normalized (a store deriva o id da chave natural); o índice UNIQUE bloqueia colisão silenciosa por bug de normalização. `item` não tem equivalente — sua chave natural inclui a source (pela aresta), assimetria aceitável.

---

## Consequências

- **Chunk é unidade de busca vetorial.** Todas as operações de KNN rodam sobre chunk; o resolver de proveniência sobe ao conhecimento (distilled).
- **Re-embed é operação mecânica e detectável.** Proveniência (modelo, dim, task_type) por record torna auditável o custo e o detecta sem arqueologia, quando o modelo mudar.
- **Trade-off aceitável de scale:** chunking multiplica o número de vetores (vs. um-por-documento), mas o limite de input (~2k tokens) o torna obrigatório. A dimensão 768 (ADR-0006) mantém o custo por vetor 4× menor que 3072, compensando.
- **Evolução é localizada:** adicionar `memory` em fase 3 é uma migration (ADR-0007), sem mudança de lógica existente. Migrar `produced_by` de `run` para `flow` é uma migration + redirect de grafo (a lógica de busca não muda).

---

## Alternativas rejeitadas

**(a) Vetor em `distilled` conforme a spec literal** — rejeitada: incompatível com chunking obrigatório (limite de 2k tokens do modelo) e com a semântica de HNSW (um vetor por record). Teria exigido segunda estrutura de índice (fora do spec — scope creep).

**(b) Embedding opcional no chunk** — rejeitada: chunk existe para resolver KNN; sem vetor é erro de construção (garante-se que TODO chunk tem embedding, não há branching em consumidor).

**(c) Criar `memory` já na fase 1** — rejeitada: sem produtor/consumidor, é especulação (D17), fora do escopo da fase 1.

**(d) Embeddar `item` na origem** — rejeitada: exigiria worker de pré-destilação que não existe (fora do escopo da fase 1). Chunking de bruto é responsabilidade diferente e posterior; corpus fonte preservado em `item.content` (ADR-0006) é suficiente para suportar essa evolução.
