-- Migration 0041 — dispatch.items aceita record<item> (digest) e record<distilled> (report)
-- (KUBO-194, ADR-0050 §V).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.
-- NUNCA DEFINE TABLE OVERWRITE dispatch (ADR-0027 §6: perderia o resto do schema).

-- A chave de identidade do dedup de envio é o `item`, não o `distilled` (ADR-0050 §III):
-- um item redestilado ganha id de distilled novo e reentraria como inédito se a chave
-- fosse o destilado. O campo muda para aceitar ambos: digest usa record<item>, report
-- usa record<distilled> (auditoria de fontes consultadas). O tipo genérico `record`
-- aceitaria os registros antigos, mas os `dispatch.items` de digest existentes apontam
-- para distilled: a exclusão por já-enviado (chave = item) nunca casaria com eles e todo
-- item já enviado reentraria. Por isso apaga o histórico (escala pessoal, aceito).
-- Efeito colateral: o watermark de todos os destinos zera; o próximo digest de cada
-- destino usa a janela de estreia (só ontem), sem recuperação de 7 dias.
DELETE dispatch;
DEFINE FIELD OVERWRITE items ON dispatch TYPE option<array<record>>;
