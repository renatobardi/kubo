// Kubo domain data (single-owner agent atelier). Vocabulary: Flow, Persona,
// Integração, Worker, Run, Task, Board, Gate, Destilado, Entidade, Fonte.
window.KUBO_DATA = {
  owner: { name: 'Renato Bardi', email: 'renato@kubo.studio' },

  stats: { fontesAtivas: 9, itensColetados7d: 214, destilados: 68, entidades: 137 },

  // Persona presets — identity IS the emoji
  personas: [
    { id: 'p-analista',   emoji: '🔍', name: 'Analista',   executor: 'api', model: 'gpt-4o',            skills: ['web-research', 'summarize'], perms: ['read:sources', 'write:distillates'] },
    { id: 'p-arquiteto',  emoji: '🧭', name: 'Arquiteto',  executor: 'api', model: 'claude-3-5-sonnet', skills: ['plan', 'decompose'],         perms: ['read:knowledge', 'write:tasks'] },
    { id: 'p-dev',        emoji: '🛠️', name: 'Dev',        executor: 'cli', model: 'claude-3-5-sonnet', skills: ['code', 'test', 'git', 'refactor'], perms: ['read:repo', 'write:repo'] },
    { id: 'p-reviewer',   emoji: '⚖️', name: 'Reviewer',   executor: 'api', model: 'gpt-4o',            skills: ['review', 'lint'],            perms: ['read:repo', 'comment'] },
    { id: 'p-destilador', emoji: '⚗️', name: 'Destilador', executor: 'api', model: 'text-embedding-3',  skills: ['distill', 'extract-entities'], perms: ['read:items', 'write:graph'] },
    { id: 'p-operador',   emoji: '⚙️', name: 'Operador',   executor: 'cli', model: '—',                 skills: ['schedule', 'collect'],       perms: ['run:workers'] },
    { id: 'p-humano',     emoji: '🧑', name: 'Humano',     executor: 'api', model: '— (você)',          skills: ['decide', 'approve'],         perms: ['gate:all'], isHuman: true },
  ],

  // Skills catalog — edited in the UI; each save creates a NEW immutable version.
  // 'refactor' is intentionally absent to demo the missing-reference warning.
  skills: {
    'web-research': { cli: false, usedBy: ['🔍'], versions: [
      { v: 3, state: 'ativa', when: 'Jul 1, 2026', by: 'você', content: '# web-research\n\nBusca a web, lê páginas e sintetiza achados **com citação de origem**.\n\n## Regras\n- Sempre registrar a URL da fonte.\n- Máx. 5 páginas por consulta.\n- Preferir fontes primárias.' },
      { v: 2, state: 'antiga', when: 'Jun 20, 2026', by: 'você', content: '# web-research\n\nBusca a web e resume.\n\n## Regras\n- Registrar URL.\n- Máx. 8 páginas por consulta.' },
      { v: 1, state: 'antiga', when: 'Jun 2, 2026', by: 'você', content: '# web-research\n\nBusca a web e resume os resultados.' },
    ] },
    'distill': { cli: false, usedBy: ['⚗️'], versions: [
      { v: 3, state: 'proposta', when: 'há 2h', by: 'flow · Coleta diária', content: '# distill\n\nExtrai claims e entidades tipadas do item bruto.\n\n## Novo (proposto pelo flow)\n- Deduplicar claims por similaridade semântica.\n- Marcar confiança por claim.' },
      { v: 2, state: 'ativa', when: 'Jun 18, 2026', by: 'você', content: '# distill\n\nExtrai um resumo, claims e entidades do item bruto.\n\n## Regras\n- 1 destilado por item.\n- Vincular entidades ao grafo.' },
      { v: 1, state: 'antiga', when: 'Mai 30, 2026', by: 'você', content: '# distill\n\nResume o item bruto em destilado.' },
    ] },
    'code': { cli: true, usedBy: ['🛠️'], versions: [
      { v: 1, state: 'ativa', when: 'Mai 22, 2026', by: 'você', content: '# code\n\nEscreve e altera código no repositório via CLI sandboxed.\n\n## Regras\n- Rodar testes antes de propor diff.\n- Nunca commitar sem gate do dono.' },
    ] },
    'review': { cli: false, usedBy: ['⚖️'], versions: [
      { v: 2, state: 'ativa', when: 'Jun 12, 2026', by: 'você', content: '# review\n\nRevisa diffs quanto a correção, estilo e riscos.' },
      { v: 1, state: 'antiga', when: 'Mai 28, 2026', by: 'você', content: '# review\n\nRevisa código.' },
    ] },
    'summarize': { cli: false, usedBy: ['🔍'], versions: [{ v: 1, state: 'ativa', when: 'Jun 2, 2026', by: 'você', content: '# summarize\n\nResume texto longo em 3–5 linhas.' }] },
    'plan': { cli: false, usedBy: ['🧭'], versions: [{ v: 1, state: 'ativa', when: 'Jun 5, 2026', by: 'você', content: '# plan\n\nDecompõe um objetivo em tasks com dependências.' }] },
    'decompose': { cli: false, usedBy: ['🧭'], versions: [{ v: 1, state: 'ativa', when: 'Jun 5, 2026', by: 'você', content: '# decompose\n\nQuebra uma task grande em subtasks.' }] },
    'test': { cli: true, usedBy: ['🛠️'], versions: [{ v: 1, state: 'ativa', when: 'Mai 22, 2026', by: 'você', content: '# test\n\nRoda a suíte de testes e reporta falhas.' }] },
    'git': { cli: true, usedBy: ['🛠️'], versions: [{ v: 1, state: 'ativa', when: 'Mai 22, 2026', by: 'você', content: '# git\n\nOperações de versionamento (branch, diff, commit sob gate).' }] },
    'lint': { cli: false, usedBy: ['⚖️'], versions: [{ v: 1, state: 'ativa', when: 'Mai 28, 2026', by: 'você', content: '# lint\n\nAplica regras de estilo e aponta violações.' }] },
    'extract-entities': { cli: false, usedBy: ['⚗️'], versions: [{ v: 1, state: 'ativa', when: 'Mai 30, 2026', by: 'você', content: '# extract-entities\n\nExtrai entidades tipadas e relações do texto.' }] },
    'schedule': { cli: true, usedBy: ['⚙️'], versions: [{ v: 1, state: 'ativa', when: 'Abr 30, 2026', by: 'você', content: '# schedule\n\nAgenda coletas por cron.' }] },
    'collect': { cli: true, usedBy: ['⚙️'], versions: [{ v: 1, state: 'ativa', when: 'Abr 30, 2026', by: 'você', content: '# collect\n\nColeta itens de uma fonte configurada.' }] },
    'decide': { cli: false, usedBy: ['🧑'], versions: [{ v: 1, state: 'ativa', when: 'Abr 18, 2026', by: 'você', content: '# decide\n\nDecisão do dono em um gate (aprovar/rejeitar).' }] },
    'approve': { cli: false, usedBy: ['🧑'], versions: [{ v: 1, state: 'ativa', when: 'Abr 18, 2026', by: 'você', content: '# approve\n\nAprova a promoção de um artefato ou worker.' }] },
  },

  // Destilados (curated knowledge, graph nodes with provenance)
  destilados: [
    { id: 'd1', title: 'Pricing usage-based vira padrão em dev-tools', summary: 'Três concorrentes migraram para cobrança por uso no Q2, liderando com narrativa de agentes.', entities: ['Usage-based pricing', 'Vercel', 'Agentes de IA'], source: 'YouTube · Fireship', item: 'Vídeo: "The pricing shift nobody noticed" (14:02)', itemUrl: 'https://youtube.com/watch?v=xxxx', run: 'run-2291', date: 'Jul 2, 2026',
      claims: ['2 de 3 concorrentes rastreados lançaram cobrança por uso no Q2.', 'Homepage de ambos lidera com narrativa de agente.', 'Diferenciação do Kubo é orquestração de flows.'] },
    { id: 'd2', title: 'RAG sobre grafo supera vetor puro em multi-hop', summary: 'Consultas multi-hop se beneficiam de proveniência tipada; vetor puro perde a cadeia.', entities: ['GraphRAG', 'Embeddings', 'Proveniência'], source: 'RSS · arXiv cs.IR', item: 'Artigo: "Typed provenance for retrieval" (PDF)', itemUrl: 'https://arxiv.org/abs/xxxx', run: 'run-2288', date: 'Jul 1, 2026',
      claims: ['Grafo tipado melhora recall em consultas de 3+ saltos.', 'Citação de origem reduz alucinação em 22% no benchmark.'] },
    { id: 'd3', title: 'Telegram como canal de digest supera e-mail em abertura', summary: 'Digests entregues via bot têm 3x mais abertura que e-mail para público técnico.', entities: ['Telegram', 'Distribuição', 'Digest'], source: 'Post · blog interno', item: 'Post: "Onde nossos amigos leem" ', itemUrl: 'https://example.com/post', run: 'run-2280', date: 'Jun 29, 2026',
      claims: ['Abertura de digest no Telegram: 74% vs 24% e-mail.', 'Amigos preferem resumo curto + link para o destilado.'] },
    { id: 'd4', title: 'Workers CLI precisam de sandbox e budget por run', summary: 'Execuções via CLI sem teto de budget causaram 2 estouros; gate de promoção recomendado.', entities: ['Worker', 'Budget', 'Sandbox'], source: 'GitHub · issues', item: 'Issue #142: runaway CLI worker', itemUrl: 'https://github.com/kubo-labs/x/issues/142', run: 'run-2275', date: 'Jun 27, 2026',
      claims: ['2 execuções excederam budget em Jun.', 'Gate humano antes de promover worker novo evita reincidência.'] },
  ],

  entities: [
    { id: 'e1', name: 'Usage-based pricing', type: 'conceito', mentions: 12, trend: [1, 0, 2, 1, 3, 2, 4, 3, 5, 4, 6, 5], relations: [{ rel: 'compete_com', target: 'Assinatura fixa' }, { rel: 'usa', target: 'Telemetria' }] },
    { id: 'e2', name: 'GraphRAG', type: 'tecnologia', mentions: 9, trend: [0, 1, 1, 2, 1, 3, 2, 2, 3, 4, 3, 5], relations: [{ rel: 'parte_de', target: 'Conhecimento' }, { rel: 'usa', target: 'Embeddings' }] },
    { id: 'e3', name: 'Telegram', type: 'organização', mentions: 7, trend: [2, 1, 0, 1, 1, 0, 2, 1, 3, 2, 1, 3], relations: [{ rel: 'usa', target: 'Bot API' }, { rel: 'parte_de', target: 'Distribuição' }] },
    { id: 'e4', name: 'Vercel', type: 'organização', mentions: 5, trend: [0, 0, 1, 0, 1, 1, 0, 1, 2, 1, 1, 2], relations: [{ rel: 'compete_com', target: 'Netlify' }] },
    { id: 'e5', name: 'Embeddings', type: 'tecnologia', mentions: 11, trend: [1, 2, 1, 3, 2, 4, 3, 4, 5, 4, 6, 5], relations: [{ rel: 'parte_de', target: 'GraphRAG' }] },
    { id: 'e6', name: 'Fireship', type: 'pessoa', mentions: 4, trend: [0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 2, 1], relations: [{ rel: 'parte_de', target: 'YouTube' }] },
  ],

  // Flows (instances of templates) + their kanban boards
  flows: [
    { id: 'f1', name: 'Kubo web — sprint 12', template: 'dev-bmad', status: 'rodando', cast: ['🧭', '🛠️', '⚖️', '🧑'], tasksOpen: 5, budget: { used: 3.2, limit: 8 }, gate: true, created: 'Jun 24, 2026',
      states: ['backlog', 'analysis', 'in_progress', 'review', 'done', 'promoted'],
      tasks: [
        { id: 't1', title: 'Levantar requisitos do board de flows', persona: '🧭', personaName: 'Arquiteto', state: 'done' },
        { id: 't2', title: 'Modelar máquina de estados do template', persona: '🧭', personaName: 'Arquiteto', state: 'done' },
        { id: 't3', title: 'Implementar colunas do kanban', persona: '🛠️', personaName: 'Dev', state: 'in_progress' },
        { id: 't4', title: 'Componente de card de task', persona: '🛠️', personaName: 'Dev', state: 'in_progress', blocked: true },
        { id: 't5', title: 'Revisar acessibilidade dos gates', persona: '⚖️', personaName: 'Reviewer', state: 'review' },
        { id: 't6', title: 'Refinar backlog de distribuição', persona: '🧭', personaName: 'Arquiteto', state: 'backlog' },
        { id: 't7', title: 'Aprovar promoção do worker x-collector', persona: '🧑', personaName: 'Humano', state: 'review', gate: true,
          gateContext: {
            pede: 'Promover o worker x-collector de proposta para produção, ativando coleta agendada.',
            produzido: [
              'Worker x-collector rodou sob contrato por 6 execuções (5 ok, 1 rate-limit).',
              'PR #12 aberto com o código do collector e testes.',
              'Review da persona Reviewer: aprovado com 2 ressalvas (tratar 429, cobrir paginação).',
            ],
            pr: 'PR #12', prUrl: 'https://github.com/kubo-labs/x/pull/12',
            budget: '3.2 / 8',
          } },
      ] },
    { id: 'f2', name: 'Coleta diária — fontes técnicas', template: 'pipeline', status: 'rodando', cast: ['⚙️', '⚗️'], tasksOpen: 2, budget: { used: 1.1, limit: 3 }, gate: false, created: 'Mai 2, 2026',
      states: ['queued', 'collecting', 'distilling', 'stored', 'failed'],
      tasks: [
        { id: 't8', title: 'Coletar canal Fireship', persona: '⚙️', personaName: 'Operador', state: 'stored' },
        { id: 't9', title: 'Coletar feed arXiv cs.IR', persona: '⚙️', personaName: 'Operador', state: 'distilling' },
        { id: 't10', title: 'Destilar backlog de 14 itens', persona: '⚗️', personaName: 'Destilador', state: 'collecting' },
        { id: 't11', title: 'Coletar RSS blog interno', persona: '⚙️', personaName: 'Operador', state: 'queued' },
        { id: 't12', title: 'Coletar canal HN (timeout)', persona: '⚙️', personaName: 'Operador', state: 'failed', error: 'HTTP 504 no fetch' },
      ] },
    { id: 'f3', name: 'Newsletter semanal', template: 'pipeline', status: 'pausado', cast: ['⚗️', '🧑'], tasksOpen: 0, budget: { used: 0.4, limit: 2 }, gate: false, created: 'Abr 18, 2026',
      states: ['queued', 'collecting', 'distilling', 'stored', 'failed'], tasks: [] },
  ],

  // Runs (worker executions)
  runs: [
    { id: 'run-2291', worker: 'yt-collector', flow: 'Coleta diária', started: 'Jul 2, 09:02', duration: '48s', status: 'concluída', items: 6, error: null },
    { id: 'run-2290', worker: 'distiller', flow: 'Coleta diária', started: 'Jul 2, 09:04', duration: '2m 11s', status: 'rodando', items: 3, error: null },
    { id: 'run-2288', worker: 'rss-collector', flow: 'Coleta diária', started: 'Jul 1, 09:02', duration: '31s', status: 'concluída', items: 9, error: null },
    { id: 'run-2286', worker: 'hn-collector', flow: 'Coleta diária', started: 'Jul 1, 09:02', duration: '30s', status: 'falhou', items: 0, error: 'HTTP 504 — upstream timeout ao buscar https://news.ycombinator.com' },
    { id: 'run-2280', worker: 'digest-builder', flow: 'Newsletter semanal', started: 'Jun 29, 08:00', duration: '1m 05s', status: 'concluída', items: 1, error: null },
    { id: 'run-2275', worker: 'x-collector', flow: 'Coleta diária', started: 'Jun 27, 09:02', duration: '12s', status: 'falhou', items: 0, error: 'Rate limit excedido (429) — budget do run esgotado' },
  ],

  integracoes: [
    { id: 'i1', name: 'github', icon: 'git-branch', color: '#1f2328', secret: 'via env', rateLimit: '5000/h', status: 'conectada' },
    { id: 'i2', name: 'telegram', icon: 'send', color: '#0ea5e9', secret: 'via env', rateLimit: '30/s', status: 'conectada' },
    { id: 'i3', name: 'rss', icon: 'rss', color: '#f59e0b', secret: '—', rateLimit: '—', status: 'conectada' },
    { id: 'i4', name: 'smtp', icon: 'mail', color: '#6366f1', secret: 'via env', rateLimit: '200/dia', status: 'conectada' },
    { id: 'i5', name: 'youtube', icon: 'youtube', color: '#ef4444', secret: 'via env', rateLimit: '10000/dia', status: 'degradada' },
  ],

  templates: [
    { id: 'tpl1', name: 'dev-bmad', states: ['backlog', 'analysis', 'in_progress', 'review', 'done', 'promoted'], cast: ['🧭', '🛠️', '⚖️', '🧑'], gates: ['promoted'], trigger: 'manual', budget: 8 },
    { id: 'tpl2', name: 'pipeline', states: ['queued', 'collecting', 'distilling', 'stored', 'failed'], cast: ['⚙️', '⚗️'], gates: [], trigger: 'cron: 0 9 * * *', budget: 3 },
    { id: 'tpl3', name: 'research-digest', states: ['queued', 'collecting', 'distilling', 'review', 'sent'], cast: ['🔍', '⚗️', '🧑'], gates: ['sent'], trigger: 'webhook', budget: 2 },
  ],

  canais: [
    { id: 'ch1', name: 'Telegram', icon: 'send', color: '#0ea5e9', status: 'ativo', detail: '@kubo_digest_bot' },
    { id: 'ch2', name: 'E-mail (SMTP)', icon: 'mail', color: '#6366f1', status: 'ativo', detail: 'digest@kubo.studio' },
  ],

  // Fontes — de onde vem o que eu sei (visão de saúde da coleta)
  fontes: [
    { id: 'src1', name: 'Fireship', type: 'youtube', integ: 'youtube', last: 'há 2h', items: 142, health: 'ok' },
    { id: 'src2', name: 'arXiv cs.IR', type: 'rss', integ: 'rss', last: 'há 3h', items: 88, health: 'ok' },
    { id: 'src3', name: 'Blog interno', type: 'rss', integ: 'rss', last: 'há 1d', items: 24, health: 'ok' },
    { id: 'src4', name: 'Hacker News — front', type: 'site', integ: 'rss', last: 'há 6d', items: 310, health: 'sem coleta' },
    { id: 'src5', name: 'Changelog dev-tools', type: 'site', integ: 'rss', last: 'há 5h', items: 57, health: 'degradada' },
    { id: 'src6', name: 'GitHub releases (watched)', type: 'api', integ: 'github', last: 'há 1h', items: 63, health: 'ok' },
  ],

  // Artefatos configurados — digests/relatórios recorrentes
  artefatos: [
    { id: 'a1', name: 'Digest semanal', query: 'destilados dos últimos 7d marcados', destinos: ['Renato Bardi', 'Marina Alves', 'Téo Nogueira'], agenda: 'cron: 0 8 * * 1' },
    { id: 'a2', name: 'Relatório de flow', query: 'runs + gates do flow selecionado', destinos: ['Renato Bardi'], agenda: 'evento: flow concluído' },
    { id: 'a3', name: 'Boletim de fontes degradadas', query: 'fontes com saúde ≠ ok', destinos: ['Webhook ops', 'Renato Bardi'], agenda: 'cron: 0 7 * * *' },
  ],

  // Destinos — pessoas + sistemas
  destinos: [
    { id: 'r1', name: 'Renato Bardi', kind: 'pessoa', role: 'dono', channel: 'Telegram · E-mail' },
    { id: 'r2', name: 'Marina Alves', kind: 'pessoa', role: 'convidada', channel: 'E-mail' },
    { id: 'r3', name: 'Téo Nogueira', kind: 'pessoa', role: 'convidado', channel: 'Telegram' },
    { id: 'r4', name: 'Webhook ops', kind: 'sistema', sys: 'webhook', channel: 'POST ops.kubo.studio/hook' },
    { id: 'r5', name: 'Arquivo mensal', kind: 'sistema', sys: 'arquivo', channel: 'exports/ (Markdown)' },
  ],

  envios: [
    { id: 's1', kind: 'Digest semanal', channel: 'Telegram', to: 'Renato Bardi', when: 'Jun 29, 08:01' },
    { id: 's2', kind: 'Digest semanal', channel: 'E-mail', to: 'Marina Alves', when: 'Jun 29, 08:01' },
    { id: 's3', kind: 'Relatório de flow', channel: 'E-mail', to: 'Renato Bardi', when: 'Jun 28, 18:30' },
    { id: 's4', kind: 'Digest semanal', channel: 'Telegram', to: 'Téo Nogueira', when: 'Jun 22, 08:01' },
  ],
};
