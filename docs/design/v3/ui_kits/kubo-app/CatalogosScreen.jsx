// Catálogos — Integrações / Personas (+ Skills versionadas) / Templates.
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

// ── Markdown preview (mínimo: #, ##, -, **negrito**) ──────────────────────
function inlineBold(text) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => /^\*\*[^*]+\*\*$/.test(p)
    ? <strong key={i} style={{ fontWeight: 600, color: 'var(--foreground)' }}>{p.slice(2, -2)}</strong>
    : <React.Fragment key={i}>{p}</React.Fragment>);
}
function MdPreview({ content }) {
  const lines = (content || '').split('\n');
  return (
    <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--foreground)' }}>
      {lines.map((ln, i) => {
        if (ln.startsWith('## ')) return <h4 key={i} style={{ margin: '12px 0 4px', fontFamily: 'var(--font-heading)', fontSize: 14, fontWeight: 600 }}>{ln.slice(3)}</h4>;
        if (ln.startsWith('# ')) return <h3 key={i} style={{ margin: '0 0 6px', fontFamily: 'var(--font-heading)', fontSize: 16, fontWeight: 600 }}>{ln.slice(2)}</h3>;
        if (ln.startsWith('- ')) return <div key={i} style={{ display: 'flex', gap: 8, paddingLeft: 4 }}><span style={{ color: 'var(--muted-foreground)' }}>•</span><span>{inlineBold(ln.slice(2))}</span></div>;
        if (ln.trim() === '') return <div key={i} style={{ height: 8 }} />;
        return <p key={i} style={{ margin: '0 0 4px' }}>{inlineBold(ln)}</p>;
      })}
    </div>
  );
}

function VersionBadge({ state }) {
  const { Badge } = K;
  if (state === 'ativa') return <Badge>ativa</Badge>;
  if (state === 'proposta') return <Badge icon="triangle-alert">proposta pendente</Badge>;
  return <Badge variant="outline">antiga</Badge>;
}

// ── Detalhe da skill: versão ativa + editor/preview + histórico ────────────
function SkillDetail({ name, onBack }) {
  const { Card, CardContent, Badge, Button, Textarea, Icon } = K;
  const skill = window.KUBO_DATA.skills[name];
  const [versions, setVersions] = useState(() => skill ? skill.versions.map(v => ({ ...v })) : []);
  const active = versions.find(v => v.state === 'ativa') || versions[0];
  const [draft, setDraft] = useState(active ? active.content : '');
  const [mode, setMode] = useState('preview'); // preview | editar
  const [selected, setSelected] = useState(active);

  const nextV = () => Math.max(...versions.map(v => v.v)) + 1;

  const saveNewVersion = () => {
    const nv = { v: nextV(), state: 'ativa', when: 'agora', by: 'você', content: draft };
    setVersions(prev => [nv, ...prev.map(v => v.state === 'ativa' ? { ...v, state: 'antiga' } : v)]);
    setSelected(nv);
    setMode('preview');
  };
  const restore = (v) => {
    const nv = { v: nextV(), state: 'ativa', when: 'agora', by: 'você', content: v.content };
    setVersions(prev => [nv, ...prev.map(x => x.state === 'ativa' ? { ...x, state: 'antiga' } : x)]);
    setDraft(v.content); setSelected(nv); setMode('preview');
  };

  const cli = skill && skill.cli;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <button onClick={onBack} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, alignSelf: 'flex-start', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 13, color: 'var(--muted-foreground)', fontFamily: 'var(--font-sans)' }}>
        <Icon name="chevron-right" size={14} style={{ transform: 'rotate(180deg)' }} /> Atores
      </button>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Icon name="sparkles" size={18} style={{ color: 'var(--muted-foreground)' }} />
        <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 18, fontWeight: 600, letterSpacing: '-0.025em', color: 'var(--foreground)' }}>{name}</h2>
        <VersionBadge state={selected ? selected.state : 'ativa'} />
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginLeft: 'auto', fontSize: 12, color: 'var(--muted-foreground)' }}>
          Usada por: {(skill.usedBy || []).map((e, i) => <window.PersonaGlyph key={i} glyph={e} size={20} />)}
        </span>
      </div>

      {cli && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 'var(--radius-xl)', background: 'color-mix(in oklab, var(--primary) 8%, transparent)', boxShadow: '0 0 0 1px color-mix(in oklab, var(--primary) 25%, transparent)' }}>
          <Icon name="triangle-alert" size={16} style={{ color: 'var(--primary)' }} />
          <span style={{ fontSize: 13, color: 'var(--foreground)' }}>Usada por persona com executor <strong style={{ fontWeight: 600 }}>cli</strong> — mudanças afetam execuções em máquina; revise com cuidado.</span>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr', gap: 16, alignItems: 'start' }}>
        {/* Editor / preview */}
        <Card>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ display: 'inline-flex', padding: 3, gap: 2, background: 'var(--muted)', borderRadius: 'var(--radius-4xl)' }}>
                {['preview', 'editar'].map(mo => (
                  <button key={mo} onClick={() => setMode(mo)} style={{ padding: '5px 12px', border: 'none', cursor: 'pointer', borderRadius: 'var(--radius-4xl)', fontSize: 13, fontFamily: 'var(--font-sans)', textTransform: 'capitalize',
                    fontWeight: mode === mo ? 500 : 400, background: mode === mo ? 'var(--background)' : 'transparent', color: mode === mo ? 'var(--foreground)' : 'var(--muted-foreground)', boxShadow: mode === mo ? '0 0 0 1px color-mix(in oklab, var(--foreground) 8%, transparent)' : 'none' }}>{mo}</button>
                ))}
              </div>
              <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>editando a partir da v{active ? active.v : 1}</span>
              <Button size="sm" onClick={saveNewVersion} style={{ marginLeft: 'auto' }}>Nova versão</Button>
            </div>
            {mode === 'editar'
              ? <Textarea value={draft} onChange={e => setDraft(e.target.value)} rows={12} style={{ fontFamily: 'ui-monospace, monospace', fontSize: 13, minHeight: 260 }} />
              : <div style={{ minHeight: 260, padding: '4px 2px' }}><MdPreview content={mode === 'preview' && selected !== active ? selected.content : draft} /></div>}
            <p style={{ margin: 0, fontSize: 11, color: 'var(--muted-foreground)' }}>Salvar cria uma nova versão imutável — nunca sobrescreve a anterior.</p>
          </CardContent>
        </Card>

        {/* Histórico */}
        <Card>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Histórico de versões</span>
            {versions.map(v => {
              const isSel = selected && selected.v === v.v;
              return (
                <div key={v.v} onClick={() => { setSelected(v); if (mode === 'editar') setDraft(v.content); }}
                  style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 10, cursor: 'pointer', borderRadius: 'var(--radius-lg)', background: isSel ? 'var(--muted)' : 'transparent', boxShadow: isSel ? '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)' : 'none' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>v{v.v}</span>
                    <VersionBadge state={v.state} />
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--muted-foreground)' }}>{v.when} · {v.by}</span>
                  {v.state !== 'ativa' && v.state !== 'proposta' && (
                    <Button size="xs" variant="outline" onClick={(e) => { e.stopPropagation(); restore(v); }}>Restaurar versão</Button>
                  )}
                  {v.state === 'proposta' && (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <Button size="xs" onClick={(e) => e.stopPropagation()}>Aprovar</Button>
                      <Button size="xs" variant="destructive" onClick={(e) => e.stopPropagation()}>Rejeitar</Button>
                    </div>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function IntegracoesTab() {
  const { Card, CardContent, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  const sv = window.KUBO_STATUS;
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');
  const filtered = d.integracoes.filter(it => window.matchQuery(query, it.name, it.status, it.rateLimit));
  const ring = '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)';
  const intIcon = (it, sz = 36) => <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: sz, height: sz, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}><Icon name={it.icon} size={Math.round(sz / 2)} /></div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <window.SearchBar value={query} onChange={setQuery} placeholder="Buscar integrações por nome ou status…" />
        <window.ViewToggle value={view} onChange={setView} allowed={['list', 'grid2', 'squares']} />
      </div>
      {filtered.length === 0 ? (
        <window.EmptyState icon="search" title="Nenhuma integração encontrada" description={`Nada casa com “${query}”.`} />
      ) : view === 'squares' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
          {filtered.map(it => (
            <div key={it.id} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: 8, height: 176, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring }}>
              {intIcon(it, 44)}
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{it.name}</span>
              <Badge variant={sv(it.status)}>{it.status}</Badge>
              <span style={{ marginTop: 'auto', fontSize: 11, color: 'var(--muted-foreground)' }}>rate {it.rateLimit}</span>
            </div>
          ))}
        </div>
      ) : view === 'grid2' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
          {filtered.map(it => (
            <div key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 12, height: 84, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring }}>
              {intIcon(it)}
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{it.name}</span>
                  <Badge variant={sv(it.status)}>{it.status}</Badge>
                </div>
                <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--muted-foreground)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>rate {it.rateLimit}</p>
              </div>
            </div>
          ))}
        </div>
      ) : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {filtered.map(it => (
        <Card key={it.id} size="sm">
          <CardContent style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {intIcon(it)}
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{it.name}</span>
                <Badge variant={sv(it.status)}>{it.status}</Badge>
              </div>
              <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>
                secret: <span style={{ fontFamily: 'ui-monospace, monospace' }}>●●● {it.secret}</span> · rate {it.rateLimit}
              </p>
            </div>
          </CardContent>
        </Card>
      ))}
      </div>
      )}
    </div>
  );
}

function PersonasTab({ onOpenSkill }) {
  const { Card, CardContent, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  const exists = (s) => Boolean(d.skills[s]);
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');
  const filtered = d.personas.filter(p => window.matchQuery(query, p.name, p.executor, p.model, (p.skills||[]).join(' ')));
  const ring = '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)';
  const skillBadges = (p) => p.skills.map(s => exists(s)
    ? <button key={s} onClick={() => onOpenSkill(s)} style={{ border: 'none', background: 'transparent', padding: 0, cursor: 'pointer' }}><Badge variant="outline">{s}</Badge></button>
    : <span key={s} title="Skill referenciada não existe no catálogo"><Badge variant="destructive" icon="triangle-alert">{s}</Badge></span>);
  const execBadge = (p) => <Badge variant={p.executor === 'cli' ? 'outline' : 'secondary'}>{p.executor}</Badge>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <window.SearchBar value={query} onChange={setQuery} placeholder="Buscar atores por nome, executor, modelo ou skill…" />
        <window.ViewToggle value={view} onChange={setView} allowed={['list', 'grid2', 'squares']} />
      </div>
      {filtered.length === 0 ? (
        <window.EmptyState icon="search" title="Nenhum ator encontrado" description={`Nada casa com “${query}”.`} />
      ) : view === 'squares' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
          {filtered.map(p => (
            <div key={p.id} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: 8, height: 176, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring }}>
              <window.PersonaGlyph glyph={p.emoji} size={48} />
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{p.name}</span>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>{execBadge(p)}{p.isHuman && <Badge icon="triangle-alert">gates</Badge>}</div>
              <span style={{ marginTop: 'auto', maxWidth: '100%', fontSize: 11, color: 'var(--muted-foreground)', fontFamily: 'ui-monospace, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.model}</span>
            </div>
          ))}
        </div>
      ) : view === 'grid2' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
          {filtered.map(p => (
            <div key={p.id} style={{ display: 'flex', gap: 12, height: 128, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring, overflow: 'hidden' }}>
              <window.PersonaGlyph glyph={p.emoji} size={40} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{p.name}</span>
                  {execBadge(p)}{p.isHuman && <Badge icon="triangle-alert">gates</Badge>}
                </div>
                <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--muted-foreground)', fontFamily: 'ui-monospace, monospace' }}>{p.model}</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>{skillBadges(p)}</div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(p => (
            <Card key={p.id} size="sm">
              <CardContent style={{ display: 'flex', gap: 12 }}>
                <window.PersonaGlyph glyph={p.emoji} size={40} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{p.name}</span>
                    {execBadge(p)}{p.isHuman && <Badge icon="triangle-alert">gates</Badge>}
                  </div>
                  <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--muted-foreground)', fontFamily: 'ui-monospace, monospace' }}>{p.model}</p>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>{skillBadges(p)}</div>
                  <p style={{ margin: '8px 0 0', fontSize: 11, color: 'var(--muted-foreground)' }}>permissões: {p.perms.join(' · ')}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// Nome da persona a partir do emoji preset.
function personaName(emoji) {
  const p = window.KUBO_DATA.personas.find(x => x.emoji === emoji);
  return p ? p.name : '';
}

// #5 — Detalhe do template: máquina de estados linear, cast, trigger, budget, flows.
function TemplateDetail({ tpl, onBack }) {
  const { Card, CardContent, Badge, Button, Icon } = K;
  const d = window.KUBO_DATA;
  const usedBy = d.flows.filter(f => f.template === tpl.name);
  const STATE_LABEL = { backlog: 'Backlog', analysis: 'Analysis', in_progress: 'In progress', review: 'Review', done: 'Done', promoted: 'Promoted', queued: 'Queued', collecting: 'Collecting', distilling: 'Distilling', stored: 'Stored', failed: 'Failed', sent: 'Sent' };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <button onClick={onBack} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, alignSelf: 'flex-start', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 13, color: 'var(--muted-foreground)', fontFamily: 'var(--font-sans)' }}>
        <Icon name="chevron-right" size={14} style={{ transform: 'rotate(180deg)' }} /> Modelos
      </button>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Icon name="workflow" size={18} style={{ color: 'var(--muted-foreground)' }} />
        <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 18, fontWeight: 600, letterSpacing: '-0.025em', color: 'var(--foreground)' }}>{tpl.name}</h2>
        <Badge variant="outline" icon="clock">{tpl.trigger}</Badge>
        <Badge variant="secondary">budget {tpl.budget}</Badge>
      </div>

      {/* State machine */}
      <Card>
        <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Máquina de estados</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
            {tpl.states.map((s, i) => {
              const isGate = tpl.gates.includes(s);
              const isFail = s === 'failed';
              return (
                <React.Fragment key={s}>
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '7px 14px', borderRadius: 'var(--radius-lg)',
                    background: isGate ? 'color-mix(in oklab, var(--primary) 12%, transparent)' : 'var(--muted)',
                    color: isGate ? 'var(--primary)' : isFail ? 'var(--destructive)' : 'var(--foreground)',
                    fontSize: 13, fontWeight: isGate ? 600 : 500,
                    boxShadow: isGate ? '0 0 0 1px color-mix(in oklab, var(--primary) 35%, transparent)' : 'none' }}>
                    {isGate && <Icon name="triangle-alert" size={13} />}{STATE_LABEL[s] || s}
                  </div>
                  {i < tpl.states.length - 1 && <Icon name="chevron-right" size={14} style={{ color: 'var(--muted-foreground)' }} />}
                </React.Fragment>
              );
            })}
          </div>
          {tpl.gates.length > 0
            ? <p style={{ margin: 0, fontSize: 12, color: 'var(--muted-foreground)' }}><Icon name="triangle-alert" size={12} style={{ verticalAlign: '-1px', color: 'var(--primary)' }} /> Estados destacados exigem um gate do dono para avançar.</p>
            : <p style={{ margin: 0, fontSize: 12, color: 'var(--muted-foreground)' }}>Fluxo automático — sem gates humanos.</p>}
        </CardContent>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'start' }}>
        {/* Cast */}
        <Card>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Cast de personas</span>
            {tpl.cast.map((e, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--foreground)' }}>
                <window.PersonaGlyph glyph={e} size={26} /> {personaName(e)}
              </div>
            ))}
          </CardContent>
        </Card>

        {/* Flows usando */}
        <Card>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Flows usando este template</span>
            {usedBy.length === 0
              ? <span style={{ fontSize: 13, color: 'var(--muted-foreground)' }}>Nenhum flow instanciado ainda.</span>
              : usedBy.map(f => (
                <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--foreground)' }}>
                  <Icon name="workflow" size={15} style={{ color: 'var(--muted-foreground)' }} />
                  <span style={{ flex: 1 }}>{f.name}</span>
                  <Badge variant={window.KUBO_STATUS(f.status)}>{f.status}</Badge>
                </div>
              ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function TemplatesTab({ onOpen }) {
  const { Card, CardContent, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');
  const filtered = d.templates.filter(t => window.matchQuery(query, t.name, t.trigger));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <window.SearchBar value={query} onChange={setQuery} placeholder="Buscar modelos por nome ou gatilho…" />
        <window.ViewToggle value={view} onChange={setView} allowed={['list']} />
      </div>
      {filtered.length === 0 ? (
        <window.EmptyState icon="search" title="Nenhum modelo encontrado" description={`Nada casa com “${query}”.`} />
      ) : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {filtered.map(t => (
        <button key={t.id} onClick={() => onOpen(t)} style={{ textAlign: 'left', cursor: 'pointer', border: 'none', background: 'transparent', padding: 0 }}>
        <Card size="sm">
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{t.name}</span>
              <Badge variant="outline" icon="clock">{t.trigger}</Badge>
              <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--muted-foreground)' }}>budget {t.budget} · <span style={{ display: 'flex', gap: 3 }}>{t.cast.map((e, i) => <window.PersonaGlyph key={i} glyph={e} size={20} />)}</span></span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
              {t.states.map((s, i) => (
                <React.Fragment key={s}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, padding: '3px 8px', borderRadius: 9999,
                    background: t.gates.includes(s) ? 'color-mix(in oklab, var(--primary) 12%, transparent)' : 'var(--muted)',
                    color: t.gates.includes(s) ? 'var(--primary)' : 'var(--muted-foreground)', fontWeight: t.gates.includes(s) ? 600 : 400 }}>
                    {t.gates.includes(s) && <Icon name="triangle-alert" size={11} />}{s}
                  </span>
                  {i < t.states.length - 1 && <Icon name="chevron-right" size={12} style={{ color: 'var(--muted-foreground)' }} />}
                </React.Fragment>
              ))}
            </div>
          </CardContent>
        </Card>
        </button>
      ))}
      </div>
      )}
    </div>
  );
}

function CatalogosScreen({ section = 'Integrações' }) {
  const { PageHeader, Button } = K;
  const [skill, setSkill] = useState(null);
  const [tpl, setTpl] = useState(null);

  if (section === 'Atores' && skill) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
        <SkillDetail name={skill} onBack={() => setSkill(null)} />
      </div>
    );
  }
  if (section === 'Modelos' && tpl) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
        <TemplateDetail tpl={tpl} onBack={() => setTpl(null)} />
      </div>
    );
  }

  const meta = {
    'Integrações': { desc: 'Conexões declaradas — segredos por referência, nunca expostos.', body: <IntegracoesTab /> },
    'Atores': { desc: 'Agentes do ateliê — clique numa skill para ver e versionar.', body: <PersonasTab onOpenSkill={setSkill} /> },
    'Modelos': { desc: 'Máquinas de estado reutilizáveis que definem os boards dos fluxos. Abra um para ver o detalhe.', body: <TemplatesTab onOpen={setTpl} /> },
  };
  const m = meta[section] || meta['Integrações'];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title={section} description={m.desc}
        actions={<Button variant="outline" icon="plus">Adicionar YAML</Button>} />
      {m.body}
    </div>
  );
}
window.CatalogosScreen = CatalogosScreen;
