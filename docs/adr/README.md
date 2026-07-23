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
| [0024](0024-issues-github-dois-regimes.md) | Issues do GitHub: dois regimes (registro vs andaime wayfinder) | aceito |
| [0025](0025-source-vira-cadastro-no-db.md) | `source` vira Cadastro no DB: registro gerível pela UI que dirige a coleta | aceito |
| [0026](0026-revoga-issue-ponteiro.md) | Revoga a regra issue-ponteiro (substitui ADR-0024) | aceito |
| [0027](0027-destino-vira-cadastro-no-db.md) | `destination` vira Cadastro no DB: destinos geríveis pela UI, multi-canal, endereço no banco | aceito |
| [0028](0028-config-operacional-global-no-db.md) | Config operacional global no DB: horário do digest e pausa de distribuição editáveis pela UI | aceito |
| [0029](0029-digest-vira-sweep-de-destinos.md) | Digest vira sweep de destinos: 1 run por destino, worker por canal, reativação escolhe backlog ou recente | aceito |
| [0030](0030-destino-padrao-e-cutover-destinations.md) | Destino padrão no `settings` + fechamento do cutover: aposenta o `destinations.yaml` | aceito |
| [0031](0031-email-worker-credenciais-via-env.md) | Worker de e-mail: credenciais via env e manifest sem integração | aceito |
| [0032](0032-llm-sincrono-rota-rss-discovery.md) | LLM síncrono em rota HTTP para descoberta de feed RSS | aceito |
| [0033](0033-convite-telegram-webhook-inbound.md) | Convite de onboarding Telegram: webhook inbound + tabela `invite` extra-spec | aceito |
| [0034](0034-topologia-prd-lxc-irmao.md) | Topologia da PRD: LXC irmão no oute-server (emenda ADR-0011 §IV) | aceito |
| [0035](0035-exposicao-prd-porta-aberta-caddy.md) | Exposição da PRD: porta aberta + TLS próprio (Caddy) | aceito |
| [0036](0036-auth-prd-firebase-scrypt.md) | Autenticação da PRD: Firebase (Google + GitHub) + scrypt break-glass | aceito |
| [0037](0037-esteira-cd-build-once-promocao-gated.md) | Esteira de CD: build-once, promoção via Tailscale, gate de aprovação | aceito |
| [0038](0038-identidade-distribuicao-prd.md) | Identidade de distribuição da PRD: e-mail (Resend) + canais Telegram | aceito |
