-- KUBO-192: campo próprio de data de publicação em `item` (ADR-0050 item I).
-- Sem data ou data futura na fonte: `upsert_item` (store) resolve pra data de
-- coleta ANTES de gravar — a mesma regra também vira VALUE aqui, não só DEFAULT
-- (achado do CodeRabbit: DEFAULT só cobre campo OMITIDO, não substitui um valor
-- futuro EXPLÍCITO — VALUE é sempre avaliado), pra não quebrar/furar a regra em
-- escrita direta de `item` fora do upsert.
DEFINE FIELD OVERWRITE published_at ON item TYPE datetime
  VALUE IF $value == NONE OR $value > time::now() THEN time::now() ELSE $value END;
