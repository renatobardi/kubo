-- KUBO-193 (ADR-0051 §I): o destilador vira portão. A "nota de relevância" do ADR
-- (PT-BR, prosa de decisão) vira, em inglês no schema (CLAUDE.md), a aresta
-- `scored_for` (item -> scored_for -> tenant) — sem tenant_id à parte, o `out` já
-- É o tenant (mesmo desenho de `membership`, ADR-0015). Título gerado (ADR-0051
-- §IV) vira campo próprio em item, nunca sobrescreve item.title.
DEFINE FIELD OVERWRITE generated_title ON item TYPE option<string>;

DEFINE TABLE OVERWRITE scored_for TYPE RELATION IN item OUT tenant ENFORCED SCHEMAFULL;
DEFINE FIELD OVERWRITE score ON scored_for TYPE int ASSERT $value >= 0 AND $value <= 10;
DEFINE FIELD OVERWRITE assigned_at ON scored_for TYPE datetime DEFAULT time::now() READONLY;
