-- Migration 0013 — cutover de `dispatch.destination` para `record<destination>` (ADR-0030 §5, ticket KUBO-48).
-- Destrutiva: apaga o histórico de envios (dado de desenvolvimento, pré-lançamento, aceito)
-- ANTES de retipar o campo. Nunca invertir: retype com strings presente falha/corrompe.
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente (OVERWRITE).

-- 1. Apaga todo o histórico de envios existente.
DELETE dispatch;

-- 2. Retipa o vínculo de destino do envio para apontar de fato ao cadastro.
DEFINE FIELD OVERWRITE destination ON dispatch TYPE record<destination>;

-- 3. Recria o índice de leitura do watermark sobre o campo retipado.
DEFINE INDEX OVERWRITE dispatch_destination ON dispatch FIELDS destination;
