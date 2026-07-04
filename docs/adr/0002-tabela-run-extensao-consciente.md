# ADR-0002 — Tabela `run` como extensão consciente da spec

> Status: **aceito** · Data: 2026-07-04

## Contexto

A fase 1 precisa de log estruturado de execução dos workers (observabilidade + a "prova dos 90 dias" como teste de aceitação executável no M6). A spec funcional (§2.3) não define uma tabela de execução; as tabelas `flow`/`task` da spec só ganham corpo na fase 3.

Ponto que **contraria** (não só estende) a spec: a §2.3 já define o alvo da aresta como `distilled -[produced_by]-> flow` (linha 135). Esta decisão redireciona esse alvo temporariamente.

## Decisão

Adicionar a tabela mínima **`run`** (campos: worker, início, fim, status, métricas, erro) como extensão consciente da spec. Proveniência via aresta **`distilled -[produced_by]-> run`** na fase 1 — a spec define `produced_by -> flow`, mas `flow` não existe na fase 1, então a aresta aponta temporariamente para `run`. A migration da fase 3, ao introduzir `flow`/`task`, **restaura o alvo da spec** (`produced_by -> flow`).

## Consequências

Extensão mínima e reversível; observabilidade e proveniência desde a primeira coleta. **Contenção explícita:** uma TERCEIRA tabela extra-spec é sinal de scope creep — para tudo e reabre planejamento.

## Alternativas rejeitadas

(a) Não registrar execução — rejeitada: sem log não há prova dos 90 dias nem observabilidade de worker.

(b) Reusar `flow`/`task` da spec já na fase 1 — rejeitada: essas tabelas não existem ainda na fase 1 e trazem complexidade prematura; `run` é o mínimo que resolve.
