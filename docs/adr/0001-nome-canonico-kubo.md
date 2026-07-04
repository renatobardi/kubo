# ADR-0001 — Nome canônico Kubo

> Status: **aceito** · Data: 2026-07-04

## Contexto

Os documentos de planejamento chegaram nomeados `kobo-*` (spec, design system). "Kubo" (工房, ateliê/oficina em japonês) é o nome pretendido do projeto; "Kobo" era grafia inconsistente. Decisão do dono (2026-07-04), registrada por disciplina de ADR desde o primeiro.

## Decisão

O nome canônico é **Kubo** (código/identificadores) / **kubo** (pacote, arquivos, namespaces). Docs renomeados `kobo-*`→`kubo-*`; toda ocorrência textual `Kobo/kobo`→`Kubo/kubo` corrigida no conteúdo, inclusive em `.coderabbit.yaml` e `.claude/agents/fable-advisor.md` (referências de caminho).

## Consequências

Grep `kobo` limpo nos artefatos canônicos (resíduo só em docs que descrevem a própria renomeação e no export vendorizado `docs/design/mvp/`, cujo namespace de build `KoboDesignSystem_*` é aceito como está). Nome estável para pacote Python, imagens, schema.

## Alternativas rejeitadas

Manter "Kobo" — rejeitada porque era grafia acidental, não o nome pretendido; o dono definiu Kubo (工房) como identidade do projeto.
