# ADR-0049 — Estudos: Lição na UI, registro de estudo, ingestão de Material em background

> Status: aceito · Data: 2026-08-04
> Emenda de ADR-0043 (fecha o ciclo "a lição vive na UI"), de ADR-0047 (Emenda 3 — JS de chat) e de ADR-0048 §6 (sectionização síncrona no upload).

## Contexto

O épico KUBO-160 entregou o Tema como container de Materiais, as duas fases de conversa, os estados explícitos e o scheduler com geração de lição por IA (KUBO-161…189). A revisão do módulo em 2026-08-04 encontrou o ciclo **aberto** e três decisões vencidas.

**1. A Lição não tem UI.** O scheduler preenche `concept`, `scenario`, `application`, `recap`, `quiz` e `provenance` via persona `tutor` (`scheduler/study_lessons.py`), mas nenhuma rota ou template exibe uma lição — `grep lesson kubo/api/` só encontra o planner. O ADR-0043 escolheu "UI-centro + sino no Telegram" justamente com a frase "a lição vive na UI"; hoje a lição vive só no banco.

**2. Nada escreve `study_log`.** A tabela existe desde a migration 0023 (com índice UNIQUE por lição), o glossário define `study_log` como "o dado que torna o estudo adaptativo", e `get_topic_progress` conta lição concluída pela existência de um `study_log`. Como nenhuma rota cria o registro, três promessas caem juntas: o progresso é permanentemente `0/N`, `next_lesson` nunca avança, e o `tutor` é chamado com `misses=()` fixo — a recapitulação por erro recente nunca acontece.

**3. A ingestão de Material não cabe num request.** O upload roda, por arquivo: parse → 1 chamada LLM de sumário → até 20 chamadas LLM sequenciais de sectionizer (timeout de 30s cada, ADR-0048 §6). O ADR-0048 rejeitou o job assíncrono com o argumento de que "o particionamento cabe em segundos" — a operação real mostrou o contrário: um epub com 20 capítulos encosta em minutos, e o dono não tem retomada, nem visibilidade de falha parcial (`material` não tem estado de ingestão: sumário `None` e seções em fallback são indistinguíveis de sucesso).

**4. O JS artesanal de chat multiplicou.** A Emenda 3 do ADR-0047 abriu exceção para "~120 linhas de JS artesanal" no chat do mentor. Hoje existem **três** implementações do mesmo parser SSE dentro de `topic.html` (uma delas, `handleSSEEvent`, é código morto), e o template chegou a 676 linhas cobrindo 5 estados.

## Decisão

### I. A Lição ganha UI, e o quiz é o que fecha a lição

- Rotas novas (todas user-scoped, mesmo molde de `routes/study.py`): timeline de lições do Tema, detalhe da lição (4 blocos + proveniência apontando para as seções de origem) e submissão do quiz.
- **Concluir lição = criar `study_log`.** Não existe botão "marcar como lida" separado: a conclusão é o envio das respostas do quiz (com reação opcional `facil|ok|dificil`). Um `study_log` por lição — o índice UNIQUE existente é a garantia, e a rota traduz a violação em 400 legível em vez de deixar subir 500.
- Lição **placeholder** (registro criado, Tutor falhou) é um estado visível na UI, não uma lição vazia silenciosa.
- Tema em `running` deixa de ser beco: a tela do Tema aponta para a timeline, e a timeline é a superfície diária do estudo.

### II. O desempenho volta ao Tutor (fecha a adaptação prometida)

`misses` deixa de ser `()` fixo. O scheduler consulta os erros recentes do plano (derivados de `study_log.answers` vs. o gabarito do quiz) e os passa ao `tutor` na geração da lição seguinte. Isso **não reordena o plano** (invariante do glossário: "sem reordenar o plano") — só muda o conteúdo da lição.

### III. Material tem ciclo de vida de ingestão, e a ingestão sai do request

- `material` ganha `status` (`pending | ready | failed`), `error` e `ingested_at`.
- O upload passa a: validar formato e tamanho → gravar o arquivo → criar Material `pending` → responder. **Zero chamada de LLM no request.**
- O processamento (parse → sumário → sectionizer) roda num job de intervalo no scheduler, que consome `pending` e marca `ready` ou `failed` com motivo. **A retomada após restart é consequência de o estado viver no banco**, não de retentativa em memória — nada de fila nova (invariante 7: sem orquestrador pesado).
- A UI mostra "Processando…" com polling HTMX enquanto houver material `pending`, e motivo + "tentar de novo" quando `failed`.
- Os gates que dependem de material processado (chat do `mentor`, fechar o Tema) exigem ≥1 Material **`ready`** — antes exigiam ≥1 Material qualquer.

**Isto emenda o ADR-0048 §6** ("sectionização síncrona no upload") e revoga a alternativa "sectionização assíncrona (job)" que aquele ADR havia rejeitado. O argumento original ("cabe em segundos") era uma hipótese; a medição a refutou.

### IV. Uma implementação de chat SSE, e a tela do Tema em partials por fase

- O parser SSE vira **um** arquivo em `static/`, parametrizado por endpoint e rótulos; mentor e planner passam a consumi-lo. A Emenda 3 do ADR-0047 continua válida como exceção à invariante 7 (HTMX não cobre SSE multi-evento), mas passa a valer para **uma** cópia — três cópias divergem, e divergiram (o bug corrigido em `e573455` nasceu exatamente disso).
- `topic.html` é quebrado em partials por fase (`_header`, `_draft`, `_planning`, `_readonly`, `_chat`). O critério não é estético: um template que cobre 5 estados torna toda mudança de uma fase um risco para as outras.
- Confirmações destrutivas passam de `confirm()` nativo para `<dialog>` do design system — que já é o padrão do gate e do wizard de RSS.
- Recusa por pré-condição aparece **antes** do clique (ex.: "Ativar" desabilitado com o motivo "defina a cadência"), não como 400 depois.

### V. O que continua como está

- Estados do Tema, fronteira de congelamento e transições reversíveis (ADR-0047 §3) — inalterados.
- Seção como átomo do plano (ADR-0048 §7) — inalterado.
- Gate humano na ativação do Plano (invariante 5) — inalterado.
- Sino no Telegram continua **outbound-only** (ADR-0043): o quiz é respondido na UI, não no chat.

## Consequências

**Positivas**
- O módulo passa a ter valor de uso: hoje o dono consegue planejar um estudo e não consegue estudar.
- Progresso, streak e atraso passam a ser deriváveis de dado real (o glossário já dizia que meta é derivação, não entidade).
- Upload deixa de ser refém do LLM e do timeout do proxy; falha parcial fica visível e retentável.
- Uma cópia do parser SSE em vez de três.

**Negativas (trade-offs)**
- **Perda de imediatismo no upload**: o erro de parse deixa de aparecer no ato. Mitigado por `failed` + motivo na lista, mas é uma regressão real de feedback.
- **Mais superfície de estado**: `material.status` é um ciclo de vida novo para manter coerente com os gates de fase.
- **Custo de IA sobe**: `misses` reais e retentativa de lição placeholder aumentam chamadas ao `tutor`. Sem cap novo (uso pessoal, ADR-0047 §1), mas fica registrado.
- **Testes de template quebram legitimamente** no redesenho: `tests/api/test_study_*.py` assertam sobre o HTML atual. Serão reescritos, não afrouxados.

**Neutras**
- Migration com backfill (`ready` para material que já tem seções) em vez de wipe: o wipe do ADR-0047 §7 foi justificado por mudança de schema incompatível; aqui o dado é aproveitável.

## Alternativas rejeitadas

- **Botão "marcar lição como lida" separado do quiz**: dois caminhos de conclusão, duas fontes de verdade para progresso, e o quiz (que alimenta a adaptação) viraria opcional na prática.
- **Quiz no Telegram por botões**: já rejeitado no ADR-0043 (expandiria o Telegram de sino a interface); nada mudou.
- **Fila dedicada (Celery/RQ/Redis) para a ingestão**: viola a invariante 7 e o escopo negativo da spec §1.2 para um único job de um usuário só.
- **`BackgroundTasks` do FastAPI como mecanismo único**: morre com o processo da API e não deixa rastro — o material ficaria `pending` para sempre depois de um deploy. O estado no banco + job de intervalo é o que dá retomada.
- **Manter a ingestão síncrona só melhorando o feedback**: não resolve o timeout do proxy, que é o modo de falha real relatado.
- **Reescrever a tela do Tema como componente rico (Alpine/SPA)**: invariante 7; o problema é organização de template, não falta de framework.
- **Deixar `misses` fixo e adaptar a lição só pela reação (fácil/ok/difícil)**: a reação é autorrelato; o erro no quiz é evidência. Usar as duas é possível, começar pela evidência é o mínimo.
