# ADR-0047 — Estudos: Tema como container de múltiplos Materiais, fases de conversa e estados explícitos

> Status: aceito · Data: 2026-08-01
> Emenda de ADR-0043 (sem revogar o domínio de 1ª classe — reverte a relação 1:1 Material↔Tema, o cap de 3 planos ativos e o fluxo direto "criar tema" sem conversa).

## Contexto

O ADR-0043 definiu Estudos como domínio de 1ª classe com fluxo **1 Material → 1 Tema → 1 Plano → N Lições** e cap de 3 planos ativos por usuário. A sessão de grilling de 2026-08-01, tomando o NotebookLM como referência de experiência, expôs três limitações concretas:

1. **Material único é estreito demais.** Um estudo real agrega vários materiais (livro + artigo complementar + outro livro da mesma área). O 1:1 obriga a criar um Tema por Material, fragmentando o estudo.
2. **Fluxo direto sem conversa é rígido.** O caso `agentic-coding-transformation-guide.pdf` mostrou: a persona `planner` gerou tema/plano ruim e não havia como pedir ajuste — só um botão "Criar tema" que dispara direto. O dono quer conversar com a IA antes de fechar o Tema (intenção, foco, nome) e antes de ativar o Plano (agrupamento, cadência, profundidade).
3. **Estados implícitos confundem a fronteira de congelamento.** O invariante 4 (snapshot do Plano) dispara na geração da 1ª lição, mas esse momento não é visível no estado do Tema — "posso editar o plano?" virou "depende".

## Decisão

### 1. Tema vira container de N Materiais (reverte o 1:1 do ADR-0043)

- **Tema** deixa de nascer de um Material único. Nasce **vazio** (`draft`); o dono adiciona Materiais (epub/PDF) dentro dele.
- **Material** é **exclusivo a um Tema** (N:1). Não há biblioteca global de Materiais — cada Material pertence a um Tema e só a ele. Reuso futuro de PDF entre Temas é re-subir, não compartilhar registro.
- A constraint `UNIQUE topic_material` (migration 0022) cai.
- Limite configurável de Materiais ativos por Tema: `KUBO_TOPIC_MAX_MATERIALS` (default 5). Material arquivado não existe (arquivar é ação de Tema, não de Material); o limite conta só Materiais associados ao Tema.
- **Cap de 3 planos ativos (ADR-0043) removido.** Uso pessoal, usuário único — o cap era proteção opcional, não necessária. Sem cap, o scheduler gera lições para todos os Temas `running`.

### 2. Duas fases de conversa com IA (chat persistido, síncrono com streaming SSE)

O fluxo direto "criar tema → planner propõe → ativar" vira duas fases distintas, cada uma com chat síncrono (streaming) e histórico persistido:

- **Fase 1 — Tema (`draft`):** dono adiciona Materiais (dropzone drag-and-drop, múltiplos arquivos = múltiplos Materiais) e conversa com a persona **`mentor`** (nova). Chat livre + campos estruturados opcionais (foco, profundidade, contexto de trabalho — `mentor` infere da conversa se vazios). `mentor` recebe **metadados + sumário** de cada Material (sumário gerado síncrono no upload, junto ao parse de capítulos) e sugere o **nome do Tema**; o dono edita inline no topo (editável em todos os estados não-arquivados). Chat do `mentor` fica desabilitado até existir ≥1 Material. Fechar o Tema (exige ≥1 Material) dispara a Fase 2 automaticamente.
- **Fase 2 — Plano (`planning`):** a persona **`planner`** (existente) propõe o Plano automaticamente ao fechar o Tema, recebendo **campos estruturados + resumo da conversa com `mentor`** (não o transcript cru). O dono revisa com **chat livre + visualização editável do Plano** coexistindo incrementalmente: edição manual e chat acumulam sobre o estado atual; "repropor tudo" é comando explícito que usa input inicial + conversa da Fase 2. A cada mensagem que tocar o Plano, `planner` retorna texto + Plano atualizado (UI re-renderiza). `planner` usa **sumários + estrutura de capítulos** (conteúdo completo é pra geração de Lição, não pra planejamento).

**Janela deslizante com resumo** em ambas as fases: turnos recentes em detalhe + resumo dos anteriores (padrão `distilled`). Custo de prompt controlado, contexto preservado.

### 3. Estados explícitos do Tema

`draft` → `planning` → `scheduled` → `running` → `archived`

| Estado | Significado | Editável |
|---|---|---|
| `draft` | Fase 1: adicionando Materiais, conversando com `mentor` | Tudo (Materiais, foco, nome, Plano não existe) |
| `planning` | Fase 2: `planner` propôs Plano, dono revisa/conversa | Plano (chat + manual), Materiais via volta a `draft` |
| `scheduled` | Plano ativado, 1ª lição ainda não gerada | Plano (reversível a `planning`), Materiais imutáveis |
| `running` | 1ª lição gerada — **congelado** (invariante 4) | Só nome do Tema |
| `archived` | Pausado, scheduler não gera lições | Só leitura (desarquivar retoma) |

**Transições reversíveis (pré-congelamento):** `planning` ↔ `draft`; `scheduled` ↔ `planning`.
**Fronteira de congelamento:** `scheduled` → `running` (1ª lição gerada pelo scheduler) é irreversível. A partir de `running`, Materiais são imutáveis e o Plano é snapshot.

### 4. Arquivar e deletar

- **Arquivar** é ação de **Tema**: oculta da lista ativa, pausa o scheduler (não gera novas lições), preserva tudo. Desarquivar retoma. Tema arquivado não conta em cap (cap removido, mas o princípio vale se voltar).
- **Deletar** é ação de **Tema ou Material**:
  - **Tema:** cascade total (Materiais + arquivos no volume + Plano + Lições + conversas) com **confirmação reforçada** mostrando o que será perdido.
  - **Material:** remove arquivo + registro. Se o Tema está em `planning` (Plano gerado mas não congelado), exige **regenerar o Plano** antes de reativar. Se o Tema está em `scheduled`/`running` (congelado), **Material é imutável** — deletar bloqueado.

### 5. UX

- **Lista de Temas:** ativos + aba/filtro de arquivados. Cada item mostra nome, estado, progresso (lições fechas/total), próxima lição. Botão "Novo estudo" cria Tema vazio em `draft`.
- **Tela do Tema em `draft`:** nome editável no topo + dropzone (drag-and-drop, lista de Materiais com remover) + chat do `mentor` (desabilitado até ≥1 Material). Tudo na mesma tela, sem ordem imposta.
- **Tela do Tema em `planning`:** nome no topo + visualização editável do Plano + chat do `planner`.
- **Dropzone:** substitui o `<input type="file">` nativo ("Choose file"). Estilizada com os tokens do design system, drag-and-drop real, seleção múltipla, lista de arquivos adicionados com nome/tamanho/botão remover antes de confirmar.

### 6. Persona `mentor` (nova, semeada por default)

- Catálogo de personas é por-tenant (ADR-0042), semeado por defaults em código. `mentor` entra como default junto com `planner` na criação do tenant.
- Papel: entender intenção, sugerir nome do Tema, refinar foco/expectativas. Recebe metadados + sumários (não conteúdo completo). Transversal ao `work_context` do usuário (consumido automaticamente, sem repetir).

### 7. Migração: recomeço limpo

Os Temas/Materiais/lições/conversas existentes (1:1 do ADR-0043) são **descartados**. Não há migração de dados — o schema muda (constraint `UNIQUE` removida, novos estados, nova entidade de conversa) e o dono recria do zero. Justificado: uso pessoal, usuário único, impacto só do dono.

## Consequências

- **Positivas:** modelo de Estudos alinha com a intuição real de estudo (agregar materiais); conversa com IA em duas fases resolve o caso "plano ruim sem poder pedir ajuste"; estados explícitos tornam a fronteira de congelamento visível; UX da dropzone acompanha a mudança de modelo (múltiplos arquivos).
- **Negativas (trade-offs):** nova entidade de **Conversa** persistida (schema + store); nova persona `mentor` no catálogo; sumário gerado no upload adiciona latência e custo de IA ao upload (aceito: previsível, só uma vez por Material); janela deslizante de chat adiciona complexidade de runtime (resumo dos turnos antigos); sem cap de Temas ativos, risco de sobrecarga cognitiva e custo de IA fica por conta do dono (aceito: uso pessoal).
- **Invariantes preservados:** SurrealDB único (invariante 2 — Conversa é tabela no banco único); gate humano no Plano (invariante 5 — dono ativa explicitamente); snapshot do Plano no congelamento (invariante 4 — `scheduled` → `running` é a fronteira); segredos só por env (invariante 8); catálogos por-tenant em código (ADR-0042 — `mentor` é default em código).
- **ADR-0043 emendado, não revogado:** Estudos continua domínio de 1ª classe fora do pipeline de coleta. As mudanças são: relação Material↔Tema (1:1 → N:1), cap de planos ativos (removido), fluxo de criação (direto → duas fases com chat), estados (implícitos → explícitos). O restante do ADR-0043 (user-scoped, meta como derivação, Telegram outbound-only, volume opaco) permanece.

## Alternativas rejeitadas

- **Novo conceito acima do Tema ("Estudo" ou "Notebook" contendo Temas):** adiciona camada sem valor na fase 1. Tema já é a unidade de estudo — promovê-lo a container de N Materiais é a mudança mínima. Rejeitada no grilling.
- **Material como biblioteca reusável (multi-Tema):** complexidade de "Material compartilhado entre Temas" (arquivar/deletar um Material em 2 Temas — qual efeito?). Material exclusivo a um Tema é mais simples; reuso futuro é re-subir.
- **Tipos de fonte além de epub/PDF (URL, texto colado, YouTube, Google Docs):** escopo de fase 1 grande demais. YouTube/Docs exigem OAuth e parsers de transcrição. Fica para depois.
- **Conversa única contínua (Fase 1 e 2 misturadas):** mistura responsabilidades (Tema ≠ Plano) e dificulta saber "o que estou ajustando agora". Duas fases distintas isolam onde o problema está.
- **Chat efêmero (só artefatos finais):** reabrir a Fase 2 pra ajustar perderia contexto — frustrante no caso "pedir pra arrumar". Conversa persistida permite continuidade.
- **`planner` conduz ambas as fases:** inflaria a persona com responsabilidade de "entender intenção e sugerir nome", diluindo seu escopo. Persona nova (`mentor`) mantém o princípio de persona = papel específico.
- **Edição manual do Plano sem chat:** ignora o caso relatado ("pedir pra arrumar"). Chat + edição manual coexistem.
- **`planner` lê conteúdo completo dos capítulos:** 5 PDFs × 20 capítulos = 100 capítulos no prompt — estoura contexto ou custa muito. Sumários + estrutura são suficientes pra planejamento; conteúdo completo é pra geração de Lição.
- **Migração automática dos Temas existentes (1:1 → N:1):** descartar e recriar é mais simples e o impacto é só do dono (uso pessoal, usuário único).

## Emendas

### Emenda 1 — Persona `summarizer` (KUBO-162, 2026-08-01)

O §6 previa apenas a persona `mentor`. A fatia KUBO-162 (sumário de Material no upload) introduziu a persona `summarizer` como split do `mentor`: `summarizer` faz sumarização de Material (síncrono, JSON estruturado); `mentor` faz conversa na Fase 1 (streaming, texto livre). O split mantém o princípio de persona = papel específico e evita sobrecarregar `mentor` com dois modos de operação. `summarizer` é default em código (`catalog_defaults.py`), mesma regra do `mentor` (ADR-0042).

### Emenda 2 — Scheduler de lições adiada para KUBO-168 (2026-08-01)

O §1 afirma "sem cap, o scheduler gera lições para todos os Temas `running`". A implementação do scheduler de lições (sino diário, geração de lição, progresso) existia no ADR-0043 e foi **temporariamente removida** no PR do epic KUBO-160 para simplificar o recomeço limpo do schema. A restauração é rastreada em **KUBO-168** — o scheduler será reescrito sobre o schema novo (Tema container, estados explícitos) quando a Fase 2 (Plano) for implementada. Sem Temas `running` ainda (Fase 2 não implementada), a remoção não causa regressão funcional.

### Emenda 3 — Exceção: JS artesanal para SSE no chat (KUBO-163, 2026-08-01)

A invariante 7 ("sem UI rica na fase 1") e a stack HTMX não cobrem streaming SSE com múltiplos eventos (chunk/done/error). O HTMX SSE extension (`hx-sse`) não suporta parsing de JSON no evento `done` nem evento `error`. Exceção registrada: o chat do mentor usa ~120 linhas de JS artesanal (parser SSE manual, `fetch`+`location.reload()` para aplicar sugestões). Demais interações do módulo Estudos continuam em HTMX.

### Emenda 4 — Janela deslizante sem resumo (KUBO-163, 2026-08-01)

O §2 prevê "janela deslizante com resumo dos turnos anteriores (padrão `distilled`)". A implementação atual trunca por `_MAX_HISTORY_CHARS` (descarta turnos antigos sem resumir). O resumo dos turnos antigos fica para KUBO-168 (junto com a Fase 2, que também precisa de resumo da conversa com `mentor`). A truncagem é aceitável na Fase 1 porque a conversa é curta (definição de Tema, não revisão de Plano).

### Emenda 5 — Scheduler de lições: split KUBO-166 × KUBO-168 (2026-08-02)

A Emenda 2 dizia que a restauração do scheduler de lições seria rastreada em **KUBO-168**. Na implementação, o trabalho foi **splitado**:

- **KUBO-166** (esta fatia) restaura a **estrutura** do scheduler: dois jobs em código (`study_transition` 06:00, `study_lesson` 07:00), transição atômica `scheduled→running` + criação de 1ª lição (registro vazio), geração de próxima lição (registro vazio) na véspera do próximo dia de cadência. Os jobs filtram só `running` (não `scheduled`/`archived`); a 1ª lição é parte da transição, não da geração regular. Tolerante a downtime (transição dispara na véspera OU depois). A transição é atômica via `transition_to_running` (CREATE lesson + UPDATE topic.state='running' numa transação, com CAS `AND state='scheduled'`).
- **KUBO-168** traz a **geração com IA** sobre o registro vazio: `concept`, `scenario`, `application`, `quiz` a partir do `plan_entry` + capítulos. Sem IA, a lição é um placeholder com `scheduled_for` — o scheduler de KUBO-166 não chama LLM.

A reversão `scheduled→planning` (botão "Editar plano") é atômica via `deactivate_plan` (reverte `status='proposed'` + `activated_at=NONE` + `state='planning'` com CAS `AND state='scheduled'`). Se o scheduler já transicionou para `running`, o CAS falha e a rota devolve 400 (`_TOPIC_FROZEN`).
