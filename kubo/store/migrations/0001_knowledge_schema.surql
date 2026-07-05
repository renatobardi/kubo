-- Migration 0001 — schema de conhecimento (spec §2.3) + run (ADR-0002).
-- Desvios registrados no ADR-0008: chunk-como-registro (o vetor mora no chunk,
-- não em distilled); memory/grounded_in adiados (D17); item preservado mas NÃO
-- embeddado. Nomes de tabela/aresta EXATAMENTE como a spec (inglês).
--
-- Postura SCHEMAFULL: entrada coletada é hostil por padrão; o schema é a primeira
-- borda. FLEXIBLE só em payload de forma variável (metadata bruto, stats, error).
-- O runner envolve o arquivo em BEGIN;...;COMMIT; (ADR-0007) — sem controle de
-- transação aqui. DDL idempotente via OVERWRITE.

-- ── source (origem: canal YT, feed RSS, site, API) ─────────────────────────
DEFINE TABLE OVERWRITE source SCHEMAFULL;
DEFINE FIELD OVERWRITE kind       ON source TYPE string;
DEFINE FIELD OVERWRITE canonical  ON source TYPE string;
DEFINE FIELD OVERWRITE title      ON source TYPE option<string>;
DEFINE FIELD OVERWRITE created_at ON source TYPE datetime DEFAULT time::now() READONLY;
-- Chave natural: identificador canônico. O record ID é derivado dela na store
-- (UPSERT determinístico); o índice UNIQUE é a rede de segurança na borda.
DEFINE INDEX OVERWRITE source_canonical ON source FIELDS canonical UNIQUE;

-- ── item (unidade coletada bruta: vídeo, post, artigo) ─────────────────────
DEFINE TABLE OVERWRITE item SCHEMAFULL;
DEFINE FIELD OVERWRITE external_id  ON item TYPE string;
DEFINE FIELD OVERWRITE url          ON item TYPE option<string>;
DEFINE FIELD OVERWRITE title        ON item TYPE option<string>;
-- Corpus fonte sempre preservado (ADR-0006): o conteúdo bruto nunca é descartado.
DEFINE FIELD OVERWRITE content      ON item TYPE string;
DEFINE FIELD OVERWRITE metadata     ON item TYPE option<object> FLEXIBLE;
DEFINE FIELD OVERWRITE collected_at ON item TYPE datetime DEFAULT time::now() READONLY;

-- ── distilled (destilado: resumo + claims; entidades via aresta mentions) ──
DEFINE TABLE OVERWRITE distilled SCHEMAFULL;
DEFINE FIELD OVERWRITE summary    ON distilled TYPE string;
DEFINE FIELD OVERWRITE claims     ON distilled TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE created_at ON distilled TYPE datetime DEFAULT time::now() READONLY;

-- ── chunk (ADR-0008: unidade de embedding; UM vetor por registro; KNN aqui) ─
DEFINE TABLE OVERWRITE chunk SCHEMAFULL;
DEFINE FIELD OVERWRITE text      ON chunk TYPE string;
-- `seq` (não `order`): evita colisão com a palavra reservada em toda query/shell (ADR-0008).
DEFINE FIELD OVERWRITE seq       ON chunk TYPE int;
-- array de tamanho fixo: um vetor de dimensão errada é rejeitado na borda.
DEFINE FIELD OVERWRITE embedding ON chunk TYPE array<float, 768>;
-- Proveniência de embedding (ADR-0006): a tripla (modelo, dim, task_type) torna
-- o vetor comparável e o re-embed um mecanismo detectável, não arqueologia.
DEFINE FIELD OVERWRITE model     ON chunk TYPE string;
DEFINE FIELD OVERWRITE dim       ON chunk TYPE int;
DEFINE FIELD OVERWRITE task_type ON chunk TYPE string;

-- ── entity (conceito/pessoa/tecnologia/organização) ────────────────────────
DEFINE TABLE OVERWRITE entity SCHEMAFULL;
DEFINE FIELD OVERWRITE name       ON entity TYPE string;
-- normalized = NFC + casefold + colapso de whitespace (match exato, sem fuzzy).
DEFINE FIELD OVERWRITE normalized ON entity TYPE string;
DEFINE FIELD OVERWRITE kind       ON entity TYPE option<string>;
DEFINE INDEX OVERWRITE entity_normalized ON entity FIELDS normalized UNIQUE;

-- ── run (ADR-0002: log de execução de worker) ──────────────────────────────
-- A forma do erro estruturado NÃO se crava aqui — o contrato de worker (M4) a define.
DEFINE TABLE OVERWRITE run SCHEMAFULL;
DEFINE FIELD OVERWRITE worker      ON run TYPE string;
DEFINE FIELD OVERWRITE started_at  ON run TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE finished_at ON run TYPE option<datetime>;
DEFINE FIELD OVERWRITE status      ON run TYPE string;
DEFINE FIELD OVERWRITE stats       ON run TYPE object FLEXIBLE DEFAULT {};
DEFINE FIELD OVERWRITE error       ON run TYPE option<object> FLEXIBLE;

-- ── arestas (RELATION com endpoints tipados) ───────────────────────────────
-- ENFORCED: proveniência é o produto — uma aresta para endpoint inexistente
-- quebraria a prova dos 90 dias em silêncio. O banco garante que os dois pontos existem.
DEFINE TABLE OVERWRITE from_source  TYPE RELATION IN item      OUT source    ENFORCED SCHEMAFULL;
DEFINE TABLE OVERWRITE derived_from TYPE RELATION IN distilled OUT item      ENFORCED SCHEMAFULL;
DEFINE TABLE OVERWRITE mentions     TYPE RELATION IN distilled OUT entity    ENFORCED SCHEMAFULL;
DEFINE TABLE OVERWRITE chunk_of     TYPE RELATION IN chunk     OUT distilled ENFORCED SCHEMAFULL;
-- produced_by aponta para run na fase 1 (ADR-0002); a spec restaura -> flow na fase 3.
DEFINE TABLE OVERWRITE produced_by  TYPE RELATION IN distilled OUT run       ENFORCED SCHEMAFULL;
-- relates_to.kind: a spec a define tipada; string por ora, nenhum produtor na fase 1 (ADR-0008 §V).
DEFINE TABLE OVERWRITE relates_to   TYPE RELATION IN entity    OUT entity    ENFORCED SCHEMAFULL;
DEFINE FIELD OVERWRITE kind ON relates_to TYPE string;
