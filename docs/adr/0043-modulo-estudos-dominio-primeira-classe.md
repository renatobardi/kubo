# ADR-0043 — Módulo Estudos como domínio de 1ª classe

> Status: aceito · Data: 2026-07-29

## Contexto

O dono quer estudar de forma estruturada materiais que já possui (livros técnicos em
epub/PDF), com plano, ritmo, fixação e aplicação ao próprio contexto de trabalho. O primeiro
material é *Laws of Software Engineering* (49 capítulos). A sessão de grilling de 2026-07-29
(spec KUBO-133, épico KUBO-132) definiu o desenho: Material → Tema → Plano de estudo
(persona propõe, dono ativa) → Lição diária gerada na véspera, adaptativa por desempenho →
acompanhamento com metas derivadas do plano.

Dois pontos de tensão com a spec funcional exigiram decisão explícita:

1. O pipeline existente (Fonte → Item → destilação → grafo → distribuição) já coleta,
   destila e entrega conhecimento — mas não tem estado de plano, progresso nem interação
   de estudo. Encaixar estudo ali forçaria os conceitos de coleta (Cadastro de fonte, Item,
   Sweep) a carregar semântica que não é deles.
2. O escopo negativo (§1.2) veta "ferramenta de project management standalone" — e um
   módulo com plano, timeline, metas e acompanhamento flerta com esse veto.

## Decisão

O módulo **Estudos** nasce como **domínio de 1ª classe** (`study`): conceitos próprios
(Material, Tema, Plano de estudo, Lição, Quiz, Registro de estudo — glossário no
`CONTEXT.md`), tabelas próprias e UI própria ("Estudos"), **fora** do pipeline de coleta.

A infra por baixo é integralmente a existente — nada de infra paralela:

- SurrealDB único, todo acesso via `kubo/store/` (invariante 2), tenant/user-scoped
  conforme o contrato vigente.
- Geração de conteúdo via personas/executors (LiteLLM), com o padrão propor→aprovar do
  Kubo no plano (eco do gate humano, invariante 5).
- Telegram **outbound-only** como sino (reusa a distribuição); a lição vive na UI.
- APScheduler para o job da véspera e a cutucada de atraso (regime do ADR-0010).
- `work_context` (Perfil de contexto de trabalho) é campo do **usuário**, transversal —
  Estudos é o primeiro consumidor, não o dono do conceito.

Sobre o escopo negativo: esta decisão **emenda sem revogar** o item "não é PM standalone"
da §1.2. A fronteira que preserva o veto: **meta é derivação, nunca entidade**. Precisão da
cerca: a **data-alvo proposta** é atributo do plano (congela na ativação, como cadência e
sequência — é o compromisso aprovado pelo dono); **progresso, streak, desvio e data-alvo
projetada** são sempre calculados de plano + registros, nunca armazenados. Não existem motor
de metas, kanban de estudo, dependências entre temas nem sync externo. Se um dia "meta"
precisar virar tabela, esta ADR deve ser substituída, não esticada.

**Scoping multi-tenant (ADR-0039 define workspace sem isolamento intra-tenant; estudo é dado
pessoal):** todas as entidades do Estudos — Material, Tema, Plano, Lição, Registro de estudo
— nascem **user-scoped** dentro do tenant. Nada de estudo é visível a outros membros do
workspace na fase 1 (desempenho e quiz são privacidade do estudante; o Material é uso pessoal
de obra de terceiros, e user-scoped preserva esse argumento). Compartilhamento intra-tenant é
decisão futura que reabre esta ADR, não default.

## Consequências

- **Positivas:** conceitos de coleta permanecem puros; o módulo é removível (blast radius
  contido em `study` + um campo no user); o padrão propor→aprovar e a proveniência
  (lição → capítulo do material) mantêm a identidade do Kubo; vocabulário já cravado no
  glossário evita deriva de linguagem entre as 5 fatias do épico.
- **Negativas (trade-offs):** duplicação parcial de maquinaria de geração/entrega em vez de
  reuso do pipeline (aceito: o reuso forçaria semântica alheia nos conceitos de coleta);
  primeiro armazenamento de arquivo do sistema (original do Material em volume gerenciado —
  compose + backup + env), um mini-filestore que não existia.
- **Volume de arquivos não é segundo datastore (invariante 2 preservado):** o volume guarda
  só o binário original, **opaco** — nenhum dado consultável vive nele; tudo que o sistema lê
  (capítulos/seções) vive no SurrealDB via store. Paths isolados por tenant/usuário; upload
  com limite de tamanho.
- **Segurança — primeira entrada binária user-uploaded parseada pelo sistema:** epub/PDF é
  entrada hostil por padrão (regra do repo); o parsing acontece na borda com validação, e a
  dependência nova de parsing fica sujeita a justificativa no PR + pip-audit.
- **Custo/quota LLM com cerca de volume nomeada:** geração é no máximo **1 lição/dia por
  plano ativo**, com cap de **3 planos ativos por usuário** (mesmo valor registrado na spec
  §3.6 — mudar um exige mudar o outro) — rate limit de provider já mordeu este projeto; a
  cerca nasce junto com o módulo, em BYOK o custo é do tenant.
- **Neutras/operacionais:** horários dos jobs no regime de operação existente; conteúdo de
  lição é destilação com proveniência — nunca reprodução extensa do material (obra de
  terceiros, uso pessoal, user-scoped).

## Alternativas rejeitadas

- **Encaixar no pipeline existente (epub como `kind` de fonte, lição como destilado):**
  contamina Cadastro de fonte/Item/Sweep com semântica de plano/progresso que não é deles e
  não elimina o estado novo (plano, registro, quiz) — rejeitada no grilling.
- **Só uso, sem módulo (destilação + digest diário):** não entrega plano, adaptação por
  desempenho, metas nem acompanhamento — o valor pedido está exatamente nesse estado.
- **Motor de metas explícito (entidade Meta, múltiplas metas/KPIs):** é o "PM standalone"
  que a §1.2 veta; meta derivada do plano entrega a cutucada sem abrir essa porta.
- **Telegram como centro da interação (lição no chat, quiz por botões):** expandiria a
  superfície inbound existente (ADR-0033 restringe o webhook a convites) para interação de
  estudo, e o formato chat é pobre para lição rica — rejeitada em favor de UI-centro + sino
  outbound.
