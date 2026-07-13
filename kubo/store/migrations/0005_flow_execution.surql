-- Migration 0005 — modelo de EXECUÇÃO da spec §2.3: flow, task, persona,
-- deliverable + arestas; e o campo `dispatch.artifact` (fix E1 do watermark).
-- Nenhuma tabela extra-spec nova (todas são da spec) — a contenção do ADR-0002
-- (terceira tabela extra-spec = `dispatch`) permanece intacta. Decisões: ADR-0016.
--
-- Postura SCHEMAFULL (entrada hostil é a borda); FLEXIBLE só no `snapshot` (forma
-- variável, congelada). O runner envolve em BEGIN;...;COMMIT; (ADR-0007) — sem
-- controle de transação aqui. DDL idempotente via OVERWRITE.

-- ── flow (instância de um template, com snapshot congelado — invariante 4) ──
DEFINE TABLE OVERWRITE flow SCHEMAFULL;
DEFINE FIELD OVERWRITE template_name    ON flow TYPE string;
DEFINE FIELD OVERWRITE template_version ON flow TYPE int;
-- pergunta do dono (proveniência / `kubo flow status`); o prompt do worker a recebe
-- por config, o flow não a compõe (ADR-0016 §I.5).
DEFINE FIELD OVERWRITE question         ON flow TYPE string;
-- snapshot: cópia INTEGRAL da config do template (board, transitions, deliverable,
-- triggers, cast, name, version). FLEXIBLE (forma variável). Transições leem DAQUI,
-- nunca do catálogo — reescrever o YAML não afeta um flow vivo (teste honesto R5).
DEFINE FIELD OVERWRITE snapshot         ON flow TYPE object FLEXIBLE;
DEFINE FIELD OVERWRITE created_at       ON flow TYPE datetime DEFAULT time::now() READONLY;

-- ── persona (snapshot POR FLOW da config do catálogo, congelada — ADR-0016 §II) ──
DEFINE TABLE OVERWRITE persona SCHEMAFULL;
DEFINE FIELD OVERWRITE name         ON persona TYPE string;
DEFINE FIELD OVERWRITE executor     ON persona TYPE string;
DEFINE FIELD OVERWRITE model        ON persona TYPE option<string>;
DEFINE FIELD OVERWRITE prompt       ON persona TYPE string DEFAULT "";
DEFINE FIELD OVERWRITE permissions  ON persona TYPE array<string> DEFAULT [];
-- catalog_name: proveniência (de qual persona.yaml esta cópia congelada nasceu).
DEFINE FIELD OVERWRITE catalog_name ON persona TYPE string;
DEFINE FIELD OVERWRITE created_at   ON persona TYPE datetime DEFAULT time::now() READONLY;

-- ── task (unidade de trabalho num flow) ────────────────────────────────────
-- state = string validada contra o snapshot (desvio consciente da spec board_state
-- + in_state; gatilho de reversão: estado ganhar config na 0014 → tabela entra lá).
DEFINE TABLE OVERWRITE task SCHEMAFULL;
DEFINE FIELD OVERWRITE state      ON task TYPE string;
-- run: liga a task ao run que a executou (auditoria). option: nasce sem run.
DEFINE FIELD OVERWRITE run        ON task TYPE option<record<run>>;
DEFINE FIELD OVERWRITE created_at ON task TYPE datetime DEFAULT time::now() READONLY;

-- ── deliverable (a casa do relatório no grafo — E2) ────────────────────────
-- kind + content markdown. SEM chunks/embedding: deliberadamente FORA do acervo
-- buscável (relatório como distilled seria poluição). content é derivado de
-- summaries hostis → untrusted no ponto de consumo (estende ADR-0013 §V.2).
DEFINE TABLE OVERWRITE deliverable SCHEMAFULL;
DEFINE FIELD OVERWRITE kind       ON deliverable TYPE string;
DEFINE FIELD OVERWRITE content    ON deliverable TYPE string;
DEFINE FIELD OVERWRITE created_at ON deliverable TYPE datetime DEFAULT time::now() READONLY;

-- ── arestas (RELATION ENFORCED, endpoints tipados — proveniência é o produto) ──
DEFINE TABLE OVERWRITE belongs_to  TYPE RELATION IN task OUT flow        ENFORCED SCHEMAFULL;
DEFINE TABLE OVERWRITE assigned_to TYPE RELATION IN task OUT persona     ENFORCED SCHEMAFULL;
DEFINE TABLE OVERWRITE produces    TYPE RELATION IN flow OUT deliverable ENFORCED SCHEMAFULL;
-- consults: task -> distilled (top-k recuperados). A aresta cross-schema que a spec
-- chama de "o diferencial", existindo de verdade pela primeira vez (ADR-0016 §II).
DEFINE TABLE OVERWRITE consults    TYPE RELATION IN task OUT distilled   ENFORCED SCHEMAFULL;

-- ── E1: dispatch.artifact — fix do watermark do digest (ADR-0016 §V) ───────
-- `last_dispatch_watermark` passa a filtrar artifact='digest'; um dispatch de report
-- não move o watermark do digest. DEFAULT 'digest' cobre novas linhas; o backfill
-- abaixo cobre o legado (DEFINE FIELD DEFAULT não retro-preenche em v3.1.5).
DEFINE FIELD OVERWRITE artifact ON dispatch TYPE string DEFAULT 'digest';
-- watermark passa a option<datetime>: um dispatch de report NÃO tem marca-d'água de
-- acervo (grava NULL). O digest sempre carrega uma — garantido no contrato
-- (`DispatchPayload` valida artifact='digest' ⇒ watermark obrigatório), não no schema.
DEFINE FIELD OVERWRITE watermark ON dispatch TYPE option<datetime>;
-- Backfill do legado: no instante da migration TODO dispatch existente é um digest
-- (reports nascem nesta sessão, depois da migration). Sem isto o filtro
-- artifact='digest' perderia os dispatches históricos e o watermark regrediria ao
-- bootstrap de 24h — o bug que o E1 corrige, reintroduzido pela própria correção.
-- Incondicional e seguro: migrations rodam uma vez só (registradas em `migration`).
UPDATE dispatch SET artifact = 'digest';
