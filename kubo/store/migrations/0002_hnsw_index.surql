-- Migration 0002 — índice HNSW sobre chunk.embedding (768, cosseno; ADR-0006).
-- Separada da 0001 (D5: índice à parte do schema).
--
-- ARMADILHA PRÉ-AUTORIZADA (plano 0003 §3.1.4): o runner envolve todo .surql em
-- BEGIN;...;COMMIT;, e `DEFINE INDEX HNSW` dentro de transação NÃO foi verificado
-- no spike (ADR-0007). O primeiro teste do marco é: esta migration aplica via
-- runner. Se DDL de índice não rodar em transação -> PARAR e consultar o advisor;
-- proibido improvisar flag "não-transacional".
--
-- Parâmetros HNSW (EFC/M) e a métrica saem do ADR-0006 (cosseno) + validação de DDL.
DEFINE INDEX OVERWRITE chunk_hnsw ON chunk FIELDS embedding
  HNSW DIMENSION 768 DIST COSINE TYPE F32 EFC 150 M 12;
