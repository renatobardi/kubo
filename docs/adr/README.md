# Registro de Decisões de Arquitetura (ADRs)

Toda decisão de arquitetura que contraria ou estende a especificação funcional (`docs/kubo-spec-funcional.md`) ou os invariantes do `CLAUDE.md` é registrada aqui como um ADR (Architecture Decision Record) numerado. Os ADRs são artefatos de aprendizado permanente que rastreiam o porquê das escolhas estruturais do projeto, permitindo que futuras revisões ou questões de manutenção entendam o contexto histórico.

Formato: cada ADR segue o template em `template.md`, com seções de Contexto, Decisão, Consequências e Alternativas rejeitadas — documentado em PT-BR.

## Índice de ADRs

| ADR | Título | Status |
|-----|--------|--------|
| [0001](0001-nome-canonico-kubo.md) | Nome canônico Kubo | aceito |
| [0002](0002-tabela-run-extensao-consciente.md) | Tabela `run` como extensão consciente da spec | aceito |
| [0003](0003-auth-api-bearer-estatico.md) | Auth da API — bearer token estático + security list OCI | aceito |
| [0004](0004-fluxo-git.md) | Convenções de fluxo Git | aceito |
| [0005](0005-veredito-spike-surrealdb-pins.md) | Veredito do spike SurrealDB + pins definitivos | aceito |
| [0006](0006-embeddings-gemini-001-768.md) | Embeddings — `gemini-embedding-001` @ 768, cosseno | aceito |
| [0007](0007-mecanica-migrations.md) | Mecânica de migrations do SurrealDB | aceito |
| [0008](0008-desvios-schema-conhecimento.md) | Desvios do schema de conhecimento (§2.3) | aceito |
| [0009](0009-contrato-worker.md) | Contrato de worker | aceito |
| [0010](0010-agendamento-fase-1.md) | Agendamento na fase 1 (`schedules.yaml` + APScheduler) | aceito |
| [0011](0011-topologia-deploy-oute-server.md) | Topologia de deploy no oute-server (LXD + Docker aninhado) | aceito |
| [0012](0012-import-legado-neon.md) | Import do legado NeonDB (script one-off via store) | aceito |
| [0013](0013-destilacao-e-grafo-buscavel.md) | Destilação e grafo buscável | aceito |
| [0014](0014-ui-foundation-browser-auth.md) | Fundação da UI: autenticação de browser da fase 2 | aceito |
| [0015](0015-distribuicao-dispatch-destinations.md) | Distribuição: `dispatch` + `destinations.yaml` | aceito |
| [0016](0016-personas-flow-minimo-analysis.md) | Personas + flow mínimo: template `analysis`, snapshot congelado | aceito |
| [0017](0017-dreno-pago-backlog-destilacao.md) | Dreno pago one-off do backlog + `retry-after` no regime diário | aceito |
| [0018](0018-boards-gates-ui-write-path.md) | Boards + gates + caminho de escrita da UI (gate humano no browser) | aceito |
| [0019](0019-executor-cli-github-persona-dev.md) | Executor `cli` + GitHub: a persona dev nasce (task → PR real → gate) | aceito |
| [0020](0020-metodologia-dev-aidlc.md) | Metodologia do flow dev da fase 4: AI-DLC no lugar do BMAD | aceito |
| [0021](0021-rito-promocao.md) | Rito de promoção: worker → grafo (deploy-gap, import-oráculo, gate sequencial) | aceito |
| [0022](0022-pipeline-agendado.md) | Pipeline agendado: watch list, flow por cron, agendamento de flow | aceito |
| [0023](0023-renuncia-scribe-harvest-side-effect-repo.md) | Renúncia: port de `scribe`/`harvest` e side-effect "criar repo na instanciação" | aceito |
