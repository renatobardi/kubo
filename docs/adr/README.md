# Registro de Decisões de Arquitetura (ADRs)

Toda decisão de arquitetura que contraria ou estende a especificação funcional (`docs/kubo-spec-funcional.md`) ou os invariantes do `CLAUDE.md` é registrada aqui como um ADR (Architecture Decision Record) numerado. Os ADRs são artefatos de aprendizado permanente que rastreiam o porquê das escolhas estruturais do projeto, permitindo que futuras revisões ou questões de manutenção entendam o contexto histórico.

Formato: cada ADR segue o template em `template.md`, com seções de Contexto, Decisão, Consequências e Alternativas rejeitadas — documentado em PT-BR.

## Índice de ADRs

| ADR | Título | Status |
|-----|--------|--------|
| [0001](0001-nome-canonico-kubo.md) | Nome canônico Kubo | aceito |
| [0002](0002-tabela-run-extensao-consciente.md) | Tabela `run` como extensão consciente da spec | aceito |
| [0003](0003-auth-api-bearer-estatico.md) | Auth da API — bearer token estático + security list OCI | aceito |
