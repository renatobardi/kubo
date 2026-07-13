-- Migration 0004 — tabela dispatch: fato de entrega de digest (ADR-0015).
-- Terceira tabela extra-spec (run: ADR-0002; chunk: ADR-0008) — a reabertura de
-- planejamento que a cláusula de contenção do ADR-0002 exige. NÃO é aresta:
-- registra QUE um digest saiu (ou falhou) para um destino, cobrindo até uma
-- marca-d'água. `destination` é STRING (id do destinations.yaml) — o destino é
-- YAML+env (E1), não tabela; logo sem RELATION.
--
-- SCHEMAFULL como as demais (entrada hostil é a borda). `error` FLEXIBLE tem
-- precedente exato em run.error. `items` é array de record<distilled> para
-- AUDITORIA (quem foi no digest) — não é aresta ENFORCED (sem consumidor de
-- drill-down, ADR-0008 §VI): referência pendurada após delete é aceitável.
-- `item_count` (não `count`): `count` colide com count() do SurrealQL (mesmo
-- pedágio de order->seq no ADR-0008). O runner envolve em BEGIN;...;COMMIT;
-- (ADR-0007). DDL idempotente via OVERWRITE.
DEFINE TABLE OVERWRITE dispatch SCHEMAFULL;
DEFINE FIELD OVERWRITE destination ON dispatch TYPE string;
DEFINE FIELD OVERWRITE channel     ON dispatch TYPE string;
DEFINE FIELD OVERWRITE status      ON dispatch TYPE string;
DEFINE FIELD OVERWRITE sent_at     ON dispatch TYPE datetime DEFAULT time::now() READONLY;
-- watermark = max(distilled.created_at) do conjunto SELECIONADO (ADR-0015 §III).
DEFINE FIELD OVERWRITE watermark   ON dispatch TYPE datetime;
DEFINE FIELD OVERWRITE item_count  ON dispatch TYPE int;
DEFINE FIELD OVERWRITE error       ON dispatch TYPE option<object> FLEXIBLE;
DEFINE FIELD OVERWRITE items       ON dispatch TYPE option<array<record<distilled>>>;
-- Índice de leitura do watermark: last dispatch ok por destino (ADR-0015 §III.2).
DEFINE INDEX OVERWRITE dispatch_destination ON dispatch FIELDS destination;
