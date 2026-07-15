# ADR-0020 — Metodologia do flow dev da fase 4: AI-DLC no lugar do BMAD

**Status:** aceito (2026-07-15)
**Decisor:** dono, em sessão de planejamento no Cowork; validado pelo advisor (Fable 5)
**Contexto de fase:** fases 1–3 da spec entregues; roadmap da fase 4 em `docs/sessions/fase4-roadmap.md`

## Contexto

A spec funcional (§2.5, §3.1, §4, §5) nomeia `dev-bmad` como o template do flow de desenvolvimento da fase 4 — herança do projeto Multica. A spec sempre tratou a metodologia como intercambiável ("'Projeto dev BMAD', 'Projeto dev Spec-kit' são templates diferentes do mesmo mecanismo", §3.1), mas o nome BMAD estava cravado no roadmap e no cenário canônico.

O dono usa profissionalmente um fluxo próprio — **AI-DLC v1.4** (base Matt Pocock), documentado em `docs/method/fluxo-aidlc-v1.4.md` — com 10 passos, guardrails maduros e playbooks nomeados. Manter duas metodologias (uma no trabalho, outra no ateliê) seria custo cognitivo sem ganho; o AI-DLC é ainda **mais estrito** que o que a spec pedia do BMAD (revisor com contexto limpo, agente nunca aprova, evidência de RED obrigatória).

## Decisão

1. **D40:** o template dev da fase 4 é **`dev-aidlc`**, baseado no AI-DLC v1.4 do dono. Toda menção operacional a `dev-bmad` na spec é emendada (§2.5, §3.1, §4, §5). Referências históricas ao BMAD como herança do Multica permanecem.
2. **D39:** adaptação solo — **núcleo mandatório** Grill → PRD → DAG → TDD → Review → Release → Learn; **Research, Spike e QA opcionais** por demanda. A opcionalidade é expressa EXCLUSIVAMENTE pelas transições da state machine (pular estado = transição declarada); **nunca** por campo interpretado pelo runtime (lista negativa do ADR-0016 §I intacta).
3. **Mapeamento fixado** (planilha → mecanismo Kubo):

| AI-DLC v1.4 | Kubo |
|---|---|
| Passo/Fase | Estado da state machine do board |
| Papel do Agente | Persona (prompt + executor + skills) |
| Skill/Playbook (grill-me, to-prd, to-issues, tdd, code-review...) | Skills de persona (spec §3.2); binding estado→persona→ação é CÓDIGO no FLOW_REGISTRY (ADR-0016 §IV), nunca YAML |
| Validador humano + Gate de conclusão | Gate (ADR-0018) |
| Saída/Artefato (ata, RESEARCH.md, PRD, DAG) | Deliverable no grafo (família do `kind=pr`, ADR-0019) |
| Knowledge Vault | O grafo de conhecimento do Kubo |
| 5 papéis humanos (PO/Dev Sr/Tech Lead/QA/AI Champion) | 1 dono (todos os validadores) |
| Confluence/Jira/Octane/Gluon/Devin | Board da UI + deliverables no grafo + GitHub + executor cli |
| Vínculos `blocks`/`is blocked by` do DAG | Aresta `blocks` da spec §2.3 (adiada no ADR-0016; consumidor chegou) |

4. **Regra do `consults` (obrigatória):** o AI-DLC não possui passo "consultar o que o sistema já sabe" — e a spec chama a aresta `task -[consults]-> distilled` de diferencial. Na adaptação Kubo, as personas de **Grill, Research e PRD consultam o grafo de conhecimento** (busca semântica) como parte do behavior, com arestas `consults` gravadas. Sem isso, o flow dev nasceria cego para o acervo que as fases 1–2 construíram.

## Guardas anti-DSL (herdadas e reafirmadas)

- Skill por estado no YAML = rampa de DSL — proibido. Skills pertencem à persona.
- Config por estado (timebox do Research, ~400 linhas por PR, limite de rodadas do Grill) = constantes de behavior ou config de worker, nunca campos de template.
- Campo `optional:` no template = runtime interpretando dado para decidir = proibido; a opcionalidade mora nas transições.
- Guardrails da planilha que já são invariantes do Kubo, agora por dupla fonte: agente nunca aprova (R8), teste antes do código (TDD do CLAUDE.md), sem push direto (branch protection ADR-0019), artefatos versionados (deliverables imutáveis no grafo), produção só por artefato imutável (deploy por BUILD_ID).

## Consequências

- A fase 4 executa o fluxo que o dono domina — menos atrito, mais fidelidade de supervisão.
- O cenário canônico da spec §4 é reescrito nos passos do AI-DLC (mesma essência: fonte hostil, decisões humanas no meio, promoção no fim).
- `docs/method/` passa a existir como casa de metodologia (distinta de `docs/design/`, que é UI).
- Sessões da fase 4 (roadmap): 0017 docs (esta emenda) → 0018 promoção → 0019 pipeline → 0020 Grill interativo → 0021–0023 dev-aidlc em três fatias → 0024 cenário canônico + Learn.

## Alternativas rejeitadas

- **Manter BMAD:** metodologia que o dono não usa; custo de manter dois vocabulários; nenhum ganho técnico.
- **Fidelidade total aos 10 passos mandatórios:** fadiga de complexidade para mantenedor solo — a própria planilha marca Research/Spike como opcionais; D39 estende a QA.
- **Dois templates (full + lite):** dois artefatos para manter e uma decisão extra por demanda; a opcionalidade por transição resolve com um template só.
