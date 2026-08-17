# Kubo — Glossário

Linguagem ubíqua do Kubo. Glossário, não spec: define o que os termos **são**, não como
são implementados. A fonte de escopo/conceitos continua sendo `docs/kubo-spec-funcional.md`;
aqui ficam os termos que precisaram ser afiados por decisão explícita (com ADR quando a
escolha é difícil de reverter).

## Coleta de conhecimento

**Fonte** (origem):
A coisa lá fora de onde o conhecimento é coletado — um feed RSS, um repositório do GitHub, um
canal. Identificada pelo seu endereço canônico (a URL). É referida por um [[Cadastro de fonte]],
não é ela mesma um registro no banco.
_Evite_: "source" solto para se referir ao cadastro (ver abaixo).

**Cadastro de fonte**:
O registro no grafo que representa uma [[Fonte]] que o Kubo coleta. Tem identidade própria
(um id que não é a URL), é gerido pelo dono na UI, e é o que **dirige a coleta** — o
agendador coleta as fontes a partir dos cadastros habilitados. Um cadastro pode ter sua URL
editada sem perder o histórico já coletado. Ver ADR-0025.
_Evite_: chamar o cadastro de "fonte" quando a distinção importa; "assinatura".

**Item**:
Uma unidade de conteúdo coletada de uma fonte (um post de feed, uma release). Aponta para o
[[Cadastro de fonte]] de onde veio — essa ligação é a proveniência da qual pende toda a
destilação. Um item nunca fica órfão de cadastro.

**kind**:
O tipo de uma [[Fonte]] (ex.: `rss`, `github-repo`). É a chave que decide qual coletor roda
para aquela fonte — mapeamento fixo em código, nunca configurável como dado. Ver ADR-0025
(despacho por kind).

**Sweep**:
A passada de coleta em horário fixo que varre todos os [[Cadastro de fonte|cadastros]]
habilitados e dispara um run por cadastro. Contrasta com agendamento por-fonte (adiado). O
relógio fixo diz *quando*; o cadastro diz *o quê*; o código diz *como*. Ver ADR-0025.

## Estudos

Domínio de 1ª classe do estudo pessoal do dono: o Kubo cura material em plano, gera lições
contextualizadas e acompanha o progresso. Código: `study`.

**Material**:
Um documento que o dono sobe (epub/PDF) dentro de um [[Tema]]. A ingestão extrai
capítulos/seções como dados e gera um sumário (consumido por [[mentor]] e [[Plano de estudo|planner]]);
toda [[Lição]] tem proveniência num trecho do material. **Exclusivo a um Tema** (N:1) — não há
biblioteca global de Materiais. Código: `material`. _Evite_: "livro", "arquivo", "fonte"
(fonte é da coleta).

**Estado de ingestão**:
Em que pé está o processamento de um [[Material]]: `pending` = arquivo guardado, ainda sem
capítulos/seções/sumário; `ready` = ingestão completa, o Material pode alimentar [[mentor]] e
[[Plano de estudo|planner]]; `failed` = a ingestão falhou e o motivo está registrado (o dono
retenta). É o que separa "o arquivo chegou" de "o Kubo entendeu o arquivo" — o upload responde
no primeiro, o estudo só anda no segundo. Código: `status` no `material`. Ver ADR-0049 §III.
_Evite_: "upload" como sinônimo de ingestão; "processado" sem dizer se deu certo.

**Tema**:
O **container** do estudo: nasce vazio (`draft`) e o dono adiciona N [[Material|Materiais]]
dentro dele. O nome é sugerido por [[mentor]] e editável inline em todos os estados
não-arquivados. Tem ciclo de estados explícito (ver [[Estado de Tema]]). Código: `topic`.
_Evite_: "curso", "trilha", "notebook".

**Estado de Tema**:
O ciclo de vida de um [[Tema]]: `draft` → `planning` → `scheduled` → `running` → `archived`.
`draft` = adicionando Materiais e conversando com [[mentor]] (Fase 1); `planning` = [[Plano de
estudo|planner]] propôs, dono revisa/conversa (Fase 2); `scheduled` = Plano ativado, 1ª lição
ainda não gerada (reversível a `planning`); `running` = 1ª lição gerada, **congelado**
(invariante 4 — Materiais imutáveis, Plano é snapshot); `archived` = pausado, scheduler não
gera lições (desarquivar retoma). Reversível até `running`; `running` é irreversível.

**Conversa de estudo**:
Chat síncrono (streaming) persistido, associado a um [[Tema]] (Fase 1, com [[mentor]]) ou ao
seu [[Plano de estudo]] (Fase 2, com [[Plano de estudo|planner]]). Janela deslizante com resumo
dos turnos anteriores. Reabrir uma fase continua a conversa — não recomeça. Código: `study_chat`.
_Evite_: "thread", "diálogo".

**mentor**:
Persona que conduz a Fase 1 do [[Tema]]: entende a intenção do dono, sugere o nome do Tema e
refina foco/expectativas. Recebe metadados + sumários dos [[Material|Materiais]] (não conteúdo
completo). Semeada por default no catálogo do tenant junto com `planner` (ADR-0042). Código:
`mentor`. _Evite_: "tutor", "librarian".

**Plano de estudo**:
A timeline de um [[Tema]]: sequência de lições, cadência e data-alvo. Proposto pela persona
`planner` a partir de **sumários + estrutura de capítulos** dos [[Material|Materiais]] + campos
estruturados + resumo da conversa com [[mentor]], revisado e ativado pelo dono. Na Fase 2
(`planning`), chat com `planner` e edição manual coexistem incrementalmente. A meta é derivada
dele (progresso vs. esperado, streak, atraso) — não existe entidade de meta separada.
Código: `study_plan`. _Evite_: "cronograma", "meta" como entidade.

**Lição**:
A unidade diária de estudo, gerada na véspera, em 4 blocos: conceito destilado, cenário,
aplicação no [[Perfil de contexto de trabalho]] e [[Quiz]]. Destilação com proveniência,
nunca reprodução do material. Adapta conteúdo ao desempenho recente (erro vira
recapitulação), sem reordenar o plano. Código: `lesson`. _Evite_: "aula", "capítulo".

**Quiz**:
As perguntas de fixação dentro de uma [[Lição]] (2-3 por lição). As respostas alimentam a
geração da lição seguinte. Código: `quiz`.

**Registro de estudo**:
O rastro de uma [[Lição]] estudada: conclusão, respostas do quiz e reação opcional
(fácil/ok/difícil). É o dado que torna o estudo adaptativo. **Nasce do envio do [[Quiz]]** —
não existe "marcar como lida" separado, para não haver duas fontes de verdade sobre o que
foi estudado. Um por Lição (o segundo envio é recusado, não sobrescreve o desempenho que já
alimentou a recapitulação). Código: `study_log`. Ver ADR-0049 §I.
_Evite_: "progresso" para o registro individual (progresso é o agregado derivado).

**Perfil de contexto de trabalho**:
Texto curto no perfil do usuário (`user_profile`) descrevendo seu mundo profissional.
Transversal ao Kubo: qualquer persona pode consumi-lo para contextualizar output; Estudos é
o primeiro cliente. Entra em prompts — nunca contém segredos. Código: `work_context`.
_Evite_: "bio", "perfil" solto.

## Camada LLM

**Ponto de contato LLM**:
Cada lugar do sistema que fala com um modelo. Existem três **portas de saída**, e só três:
`api` (via LiteLLM), `cli` (loop de agente via Claude Agent SDK) e `embedding` (REST do
provedor de embeddings). As portas diferem porque os mecanismos são diferentes — loop de
agente com tools não é uma completion, e vetor não é texto. Mas **todo** ponto de contato lê
sua configuração do mesmo lugar: nenhum escolhe modelo ou parâmetro por conta própria.
_Evite_: "chamada de LLM" quando a distinção entre porta e configuração importa; tratar
LiteLLM como se fosse o ponto único (ele é uma das três portas).

**Persona de sistema**:
[[Persona]] que serve ação do próprio sistema — embedding hoje, chat e outras
ações internas depois. Não entra em cast de flow e não recebe task atribuída. É obrigatória:
semeada na criação do tenant e não deletável. Pode declarar [[Campo travado|campos travados]].
_Evite_: chamá-la de "persona" solto num contexto de flow (ali persona significa papel).

**Persona de cast**:
A persona-papel da spec §3.1: papel a quem tasks são atribuídas e que um flow instancia no seu
cast. Todo cast inclui a persona Humano. Contrasta com [[Persona de sistema]], que não é papel.
_Evite_: "persona de flow" (o flow instancia, não define).

**Registry de modelos**:
Mapa `modelo → capacidades` — se o modelo aceita `temperature`, se aceita `reasoning_effort`, e
o que mais precisa ser sabido para montar uma chamada válida. É **fato sobre o provedor, não
escolha de tenant**: nenhum tenant tem opinião sobre o que um modelo aceita. Por isso vive em
código, é idêntico em todo tenant e ambiente, e **não é um quarto catálogo** (invariante 3
segue intacto). Modelo ausente do registry recebe só o mínimo seguro, nunca bloqueia o uso.
_Evite_: "catálogo de modelos" (catálogo é a coisa por-tenant que o dono edita).

**Campo travado**:
Campo de [[Persona de sistema]] visível mas não editável. Existe para o caso em que o valor é
pinado por uma decisão de outra camada — `embedder.model` é travado porque a dimensão do vetor
está pinada no schema do banco, e trocar o modelo tornaria incomparáveis os vetores já
gravados. Config que aparenta ser configurável e não é seria pior que constante.
_Evite_: "campo read-only" (a UI toda é read-only por default; travado é sobre o dado).

## Tenancy

**Tenant**:
O workspace de uma equipe ou pessoa no Kubo. Todo dado tenant-scoped pertence a exatamente um
tenant; nenhum dado de tenant tem `tenant_id` anulável. Um [[Membro]] pertence a um tenant por
meio de `membership`. Código: `tenant`. _Evite_: "workspace" quando a distinção com a UI importa;
"organização".

**Sessão de store**:
O objeto que carrega o escopo de tenant e user em operações no `kubo/store/`. Checa `membership`
uma vez na criação e injeta o predicado de tenant em toda query que emite. Substitui o repasse
manual de `tenant_id`/`user_id` em cada assinatura de store. Código: `ScopedStore`. _Evite_:
"conexão" (a conexão é o socket SurrealDB; a sessão é o escopo por cima), "contexto" solto.

## Identidade e preferências

**Conta**:
A identidade humana que se autentica no Kubo. Vive fora de qualquer tenant; pode pertencer a
vários tenants por meio de `membership`. Carrega `firebase_uid`, `email`, provedores vinculados e
meios de autenticação (Firebase, scrypt). Código: `user`. _Evite_: "Account" solto quando o
código já fala `user`.

**Perfil do usuário**:
Identidade visível e preferências globais de uma [[Conta]]. Ligado 1:1 a `user`: nome de exibição,
avatar, idioma, timezone e o [[Perfil de contexto de trabalho|contexto de trabalho]].
Código: `user_profile`. _Evite_: "perfil" solto, "profile" sem prefixo.

**Tema da interface**:
Aparência da UI (`light`, `dark`, `system`) de um [[Membro]] dentro de um tenant. Vinculado
à relação `membership`, porque um mesmo `user` pode querer aparências diferentes em tenants
diferentes. Código: `theme` no `membership`. _Evite_: guardar `theme` em `user_profile` como se
fosse global.

**Membro**:
Relação `user -> tenant`, com papel e preferências locais do workspace. Código: `membership`.
_Evite_: "membro" solto sem o contexto da relação.
