-- KUBO-192: campo próprio de data de publicação em `item` (ADR-0050 item I).
-- Sem data ou data futura na fonte: `upsert_item` (store) resolve pra data de
-- coleta ANTES de gravar — a mesma regra também vira DEFAULT aqui (espelha
-- `collected_at`), pra não quebrar escrita direta de `item` fora do upsert.
DEFINE FIELD OVERWRITE published_at ON item TYPE datetime DEFAULT time::now();
