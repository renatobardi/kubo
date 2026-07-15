# Fluxo de trabalho AI-DLC v1.4 (referência de metodologia)

> **Origem:** planilha "Fluxo de trabalho v1.4" do dono (base: Matt Pocock / AI-DLC), usada profissionalmente num contexto corporativo (tribo, Confluence/Jira/Octane/Gluon/Devin). Este documento é a conversão fiel-condensada para o repo.
> **Adaptação ao Kubo:** decidida no ADR-0020 (D39/D40). Ferramentas corporativas → board da UI + deliverables no grafo + GitHub + executor cli; 5 papéis humanos → 1 dono. As menções corporativas abaixo são preservadas como registro da origem, não como requisito.

## Visão geral

Dez passos em quatro macro-fases: **Inception** (Grill, Research, Spike, PRD), **Inception→Construction** (DAG), **Construction** (TDD, Review, QA), **Operations** (Release, Learn). No Kubo (D39): núcleo mandatório **Grill → PRD → DAG → TDD → Review → Release → Learn**; **Research, Spike e QA opcionais** por demanda.

---

## 1. Grill (Intenção & Grill) — mandatório

- **Detalhe:** o dono traz a intenção em uma frase. Em vez de spec no escuro, o agente — com o playbook `grill-me` e o contexto do domínio — entrevista **uma pergunta por vez** até zerar as decisões em aberto.
- **Gatilho:** nova intenção, problema ou oportunidade priorizada.
- **Papel do agente:** conduzir a entrevista; identificar ambiguidades, premissas, restrições, riscos, dependências e decisões pendentes; estruturar a ata **sem tomar decisões pelo humano**.
- **Skill/Playbook:** `grill-me` (ou `grill-with-docs`).
- **Validador:** dono (escopo/comportamento e viabilidade técnica).
- **Saída:** ata estruturada (intenção, contexto, decisões, premissas, escopo, fora de escopo, restrições, riscos, critérios iniciais, dúvidas resolvidas, vocabulário novo), versionada.
- **Gate de conclusão:** decisões relevantes em aberto = zero; intenção/escopo/fora-de-escopo compreendidos; ata validada.
- **Guardrails:** uma pergunta por vez; não antecipar implementação; não decidir pelo humano; premissas explícitas; diferenciar fato, hipótese e decisão; sem dados sensíveis.
- **Sessão:** mandatória, interativa, supervisionada, limitada à demanda.

## 2. Research — opcional

- **Detalhe:** o agente explora sistemas, repositórios, contratos, dependências e restrições para reduzir incertezas do Grill e evitar reinvestigação futura.
- **Gatilho:** incertezas técnicas/funcionais/arquiteturais identificadas no Grill.
- **Papel do agente:** mapear e reunir evidências; diferenciar fatos de hipóteses; propor perguntas adicionais sem decidir.
- **Skill:** `research`.
- **Saída:** `RESEARCH.md` datado, com fontes, componentes, contratos, dependências, riscos, hipóteses e pendências.
- **Gate:** incertezas críticas investigadas, fontes registradas, riscos e pendências explícitos.
- **Guardrails:** timeboxed; preferencialmente read-only; sem secrets; conhecimento efêmero não vira canônico automaticamente.
- **Sessão:** opcional, efêmera, supervisionada.

## 3. Spike (Protótipo) — opcional

- **Detalhe:** variações rápidas para testar hipótese técnica/arquitetural/de experiência antes de compromisso com a solução definitiva.
- **Gatilho:** incerteza que pesquisa/análise documental não resolvem.
- **Papel do agente:** produzir alternativas rápidas e isoladas, executar verificações, comparar contra critérios definidos.
- **Skill:** `prototype`/`spike`.
- **Saída:** resultado do experimento, critérios, limitações, decisão e justificativa; código em branch `spike/...` descartável.
- **Gate:** hipótese validada ou rejeitada; decisão registrada; descartáveis removidos.
- **Guardrails:** timebox obrigatório; sem dados reais; **código do spike nunca é promovido diretamente**; registrar alternativas descartadas e o porquê.
- **Sessão:** opcional, efêmera, isolada, supervisionada.

## 4. PRD (Product Definition) — mandatório

- **Detalhe:** transforma o alinhamento do Grill em definição de produto por **comportamentos observáveis**, escopo, fora de escopo e critérios mensuráveis.
- **Gatilho:** Grill concluído e intenção validada.
- **Papel do agente:** estruturar o PRD; transformar decisões em comportamentos e critérios verificáveis; apontar inconsistências; **não antecipar implementação**.
- **Skill:** `to-prd`.
- **Validador:** dono (comportamento + viabilidade).
- **Saída:** PRD aprovado e versionado.
- **Gate:** comportamentos, escopo, fora de escopo, critérios de aceite, restrições e métricas definidos; aprovação registrada.
- **Guardrails:** **não descrever classes, tabelas, frameworks ou implementação**; critérios verificáveis; premissas e exclusões explícitas; sem dados sensíveis.
- **Sessão:** mandatória, colaborativa, supervisionada.

## 5. DAG (Work Decomposition) — mandatório

- **Detalhe:** o PRD é decomposto em **fatias verticais executáveis**. Cada nó do grafo direcionado acíclico entrega comportamento demonstrável; vínculos `blocks`/`is blocked by` representam dependências.
- **Gatilho:** PRD aprovado e liberado para construção.
- **Papel do agente:** propor fatias verticais, identificar dependências, elaborar critérios de aceite, verificar ciclos e tamanho de contexto.
- **Skill:** `to-issues`.
- **Saída:** tasks de desenvolvimento prontas e conectadas em DAG.
- **Gate:** fatias verticais e demonstráveis; critérios definidos; dependências mapeadas; **ausência de ciclos**; cada task executável em uma sessão.
- **Guardrails:** **não criar tarefas horizontais por camada**; spike não é task de desenvolvimento; limitar WIP; resultado observável por task.
- **Sessão:** mandatória, estruturada, supervisionada, preparada para execução paralela.

## 6. TDD (Red-Green-Refactor) — mandatório

- **Detalhe:** cada task é implementada pelo ciclo teste-falhando → implementação mínima → refatoração, com o humano comandando e validando.
- **Gatilho:** task em estado pronto para desenvolvimento.
- **Papel do agente:** escrever o teste primeiro, **evidenciar o RED**, implementar o mínimo para GREEN, refatorar, executar verificações e apresentar evidências antes de declarar conclusão.
- **Skill:** `tdd` + `verify-before-done`.
- **Saída:** branch + Pull Request vinculados à task, com testes e evidências.
- **Gate:** teste criado e falhando antes da implementação; testes verdes; verificações executadas; PR aberto e vinculado.
- **Guardrails:** nenhum código de produção antes do teste falhar; não remover/enfraquecer testes; sem push direto; PRs de até ~400 linhas (401–800 exige justificativa; >800 divide, salvo exceção).
- **Sessão:** mandatória, **uma sessão por task**, supervisionada, contexto limitado ao necessário.

## 7. Code Review — mandatório

- **Detalhe:** uma sessão **independente** do agente faz o primeiro passe técnico; depois o revisor humano avalia o PR e decide.
- **Gatilho:** PR aberto, testes executados.
- **Papel do agente:** revisão independente — corretude, testes, arquitetura, segurança, contratos, aderência ao PRD, mudanças indevidas; classificar apontamentos por severidade.
- **Skill:** `code-review` + `receiving-review`.
- **Saída:** PR revisado, comentários tratados, decisão e aprovação humana registradas.
- **Gate:** checks verdes; comentários bloqueantes resolvidos; **aprovação humana registrada**.
- **Guardrails:** **sessão com contexto limpo; agente revisor ≠ implementador; agente NUNCA aprova; mínimo de uma aprovação humana**.
- **Sessão:** mandatória, independente, supervisionada, contexto limpo.

## 8. QA (Quality Validation) — opcional no Kubo

- **Detalhe:** o agente apoia plano e casos; o humano executa exploração, registra evidências e formaliza defeitos.
- **Skill:** `qa-plan`.
- **Gate:** testes executados e PASSED; exploração humana concluída; defeitos formalizados/tratados.
- **Guardrails:** exploração humana obrigatória; correções retornam ao DAG; dados mascarados/sintéticos.
- **Sessão:** supervisionada, exploratória, orientada a evidências.

## 9. Release & Deploy — mandatório

- **Detalhe:** o humano coordena publicação, release e deploy; o agente verifica rastreabilidade e apoia automações, **sem operar produção autonomamente**.
- **Gatilho:** validação aprovada, versão candidata pronta.
- **Papel do agente:** verificar integridade da cadeia, validar versões e links, consolidar evidências, apoiar execução autorizada **sem decidir ou implantar autonomamente**.
- **Skill:** `release-check` + `verify-before-done`.
- **Saída:** release registrada, publicada, implantada e rastreável, com rollback definido.
- **Gate:** cadeia completa e íntegra; artefatos imutáveis; aprovações concluídas; rollback definido.
- **Guardrails:** **produção somente por tag imutável; branch não é versão implantada; agente sem autonomia para produção**.
- **Sessão:** mandatória, controlada, auditável, aprovações humanas explícitas.

## 10. Learn & Improve — mandatório

- **Detalhe:** o ciclo é analisado para transformar resultados, falhas, feedback e decisões em melhorias **versionadas** no processo, conhecimento, arquitetura e playbooks.
- **Gatilho:** release concluída, incidente, defeito relevante ou ritual periódico.
- **Papel do agente:** consolidar aprendizados, identificar padrões, sugerir alterações em playbooks/ADRs/vault, criar propostas de mudança com evidências.
- **Skill:** `triage` + `improve-codebase-architecture`.
- **Saída:** aprendizados versionados — PRs em playbooks, ADRs, páginas do vault, issues de melhoria.
- **Gate:** aprendizados com evidência, responsável, destino e prazo; alterações aprovadas pelo owner.
- **Guardrails:** **não registrar aprendizado apenas em ata**; mudanças exigem controle de versão; evitar métricas de vaidade.
- **Sessão:** mandatória, rito periódico, supervisionada, orientada a evidências.
