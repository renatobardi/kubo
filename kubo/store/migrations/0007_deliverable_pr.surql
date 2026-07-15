-- Migration 0007 — campos estruturais do deliverable de PR (ADR-0019 §VI, E3).
-- O deliverable kind="pr" guarda a URL e o número do PR que a API do GitHub devolveu
-- (E3: estrutura vem da resposta da API, NUNCA da prosa do agente). O `content` segue
-- sendo o resumo do agente (untrusted no consumo — E4); estes campos são a ref estrutural.
--
-- option<...>: o deliverable é SCHEMAFULL e o report (kind="report") não carrega esses
-- campos — obrigatório violaria o schema dos deliverables existentes. DDL idempotente via
-- OVERWRITE; o runner envolve em BEGIN;...;COMMIT; (ADR-0007).
DEFINE FIELD OVERWRITE pr_url    ON deliverable TYPE option<string>;
DEFINE FIELD OVERWRITE pr_number ON deliverable TYPE option<int>;
