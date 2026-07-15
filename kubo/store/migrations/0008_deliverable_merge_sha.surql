-- Migration 0008 — merge_commit_sha estrutural do deliverable de PR (ADR-0021 §2/§9).
-- O rito de promoção grava, no Confirmar, o SHA do merge commit que a API do GitHub devolveu
-- (`merged:true` → `merge_commit_sha`). É a MESMA espécie de pr_url/pr_number (fato ESTRUTURAL
-- do PR vindo da API — E3), a âncora de auditoria "este código foi mesclado por este commit".
--
-- option<...>: só o deliverable `kind=pr` JÁ PROMOVIDO o carrega; report e PR não-promovido não.
-- DDL idempotente via OVERWRITE; o runner envolve em BEGIN;...;COMMIT; (ADR-0007).
DEFINE FIELD OVERWRITE merge_commit_sha ON deliverable TYPE option<string>;
