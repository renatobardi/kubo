// Kubo mobile — adaptive phone app. Same product/vocabulary as the web atelier,
// re-expressed in mobile grammar: bottom tab bar, large-title headers, sticky
// search, full-width cards, and push detail stacks. Monochrome DS tokens.
let K = window.KoboDesignSystem_6efae6;
const { useState } = React;
if (!window.matchQuery) window.matchQuery = function (query, ...fields) { const s = (query || '').trim().toLowerCase(); if (!s) return true; return fields.some(f => String(f == null ? '' : f).toLowerCase().includes(s)); };

const PGLYPH = { '🔍': 'search', '🧭': 'network', '🛠️': 'git-branch', '⚖️': 'circle-check', '⚗️': 'filter', '⚙️': 'cpu', '🧑': 'user' };
const SOURCE_ICON = (src) => {
  const s = (src || '').toLowerCase();
  if (s.includes('youtube')) return 'youtube';
  if (s.includes('github')) return 'git-branch';
  if (s.includes('post') || s.includes('blog')) return 'file';
  return 'rss';
};
const TYPE_ICON = { pessoa: 'user', tecnologia: 'cpu', 'organização': 'building-2', conceito: 'lightbulb' };

// ── shared atoms ───────────────────────────────────────────
function Glyph({ name, size = 34, icon = 20, tone = 'muted' }) {
  const { Icon } = K;
  const bg = tone === 'primary' ? 'var(--primary)' : 'var(--muted)';
  const fg = tone === 'primary' ? 'var(--primary-foreground)' : 'var(--muted-foreground)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: bg, color: fg }}>
      <Icon name={name} size={icon} />
    </div>
  );
}
function Cast({ list = [], size = 24 }) {
  const { Icon } = K;
  return (
    <div style={{ display: 'flex' }}>
      {list.map((g, i) => (
        <span key={i} style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, marginLeft: i ? -6 : 0, borderRadius: 9999, background: 'var(--muted)', color: 'var(--muted-foreground)', boxShadow: '0 0 0 2px var(--background)' }}>
          <Icon name={PGLYPH[g] || 'bot'} size={Math.round(size * 0.5)} />
        </span>
      ))}
    </div>
  );
}
function StatusDot({ status }) {
  const bad = /falh|degrad|sem coleta|error/i.test(status);
  const live = /rodando|coletando|distilling|collecting/i.test(status);
  const color = bad ? 'var(--destructive)' : live ? 'var(--foreground)' : 'var(--muted-foreground)';
  return <span style={{ width: 7, height: 7, borderRadius: 9999, background: color, flexShrink: 0, opacity: live ? 1 : 0.55 }} />;
}
function Card({ onClick, children, style }) {
  const clickable = !!onClick;
  return (
    <button onClick={onClick} disabled={!clickable} style={{ textAlign: 'left', width: '100%', cursor: clickable ? 'pointer' : 'default', display: 'flex', gap: 12, padding: 16, boxSizing: 'border-box', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', font: 'inherit', color: 'inherit', ...style }}>
      {children}
    </button>
  );
}
function SearchField({ value, onChange, placeholder }) {
  const { Input, Icon } = K;
  return (
    <div style={{ position: 'relative' }}>
      <span style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted-foreground)', pointerEvents: 'none' }}><Icon name="search" size={16} /></span>
      <Input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} style={{ paddingLeft: 34, height: 40, borderRadius: 9999, width: '100%', boxSizing: 'border-box' }} />
    </div>
  );
}
function LargeHeader({ title, subtitle }) {
  return (
    <div style={{ padding: '4px 20px 14px' }}>
      <h1 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 30, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.05, color: 'var(--foreground)' }}>{title}</h1>
      {subtitle && <p style={{ margin: '4px 0 0', fontSize: 14, color: 'var(--muted-foreground)' }}>{subtitle}</p>}
    </div>
  );
}
function SectionLabel({ children, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 20px 8px' }}>
      <span style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--muted-foreground)' }}>{children}</span>
      {right}
    </div>
  );
}
function Empty({ title, description }) {
  const { Icon } = K;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: '48px 24px', textAlign: 'center' }}>
      <Glyph name="search" size={48} icon={22} />
      <p style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{title}</p>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: 'var(--muted-foreground)', maxWidth: 260 }}>{description}</p>
    </div>
  );
}
const listWrap = { display: 'flex', flexDirection: 'column', gap: 10, padding: '0 16px' };

// ── PAINEL ─────────────────────────────────────────────────
function Painel({ push, d }) {
  const { Icon, Badge } = K;
  const stats = [
    { k: 'fontesAtivas', label: 'Fontes ativas', icon: 'rss' },
    { k: 'itensColetados7d', label: 'Itens · 7d', icon: 'database' },
    { k: 'destilados', label: 'Destilados', icon: 'book-open' },
    { k: 'entidades', label: 'Entidades', icon: 'network' },
  ];
  const gates = [];
  d.flows.forEach(f => f.tasks.forEach(t => { if (t.gate) gates.push({ flow: f, task: t }); }));
  const recent = d.runs.slice(0, 4);
  return (
    <div>
      <LargeHeader title="Painel" subtitle={`Ateliê de ${d.owner.name.split(' ')[0]}`} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, padding: '0 16px' }}>
        {stats.map(s => (
          <div key={s.k} style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 16, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 28, fontWeight: 700, fontFamily: 'var(--font-heading)', letterSpacing: '-0.03em', color: 'var(--foreground)' }}>{d.stats[s.k]}</span>
              <span style={{ color: 'var(--muted-foreground)' }}><Icon name={s.icon} size={18} /></span>
            </div>
            <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{s.label}</span>
          </div>
        ))}
      </div>

      {gates.length > 0 && <>
        <SectionLabel>Precisa de você</SectionLabel>
        <div style={listWrap}>
          {gates.map(({ flow, task }) => (
            <Card key={task.id} onClick={() => push({ type: 'gate', flow, task })} style={{ borderColor: 'color-mix(in oklab, var(--foreground) 24%, transparent)' }}>
              <Glyph name="shield" tone="primary" />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Badge variant="outline">gate</Badge>
                  <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{flow.name}</span>
                </div>
                <p style={{ margin: 0, fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{task.title}</p>
              </div>
              <Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)', alignSelf: 'center' }} />
            </Card>
          ))}
        </div>
      </>}

      <SectionLabel right={<button onClick={() => push({ type: 'runs-all' })} style={{ border: 'none', background: 'transparent', color: 'var(--muted-foreground)', fontSize: 12, fontFamily: 'var(--font-sans)', cursor: 'pointer' }}>Ver tudo</button>}>Execuções recentes</SectionLabel>
      <div style={listWrap}>
        {recent.map(r => <RunRow key={r.id} r={r} onClick={() => push({ type: 'run', data: r })} />)}
      </div>
    </div>
  );
}

// ── CONHECIMENTO (destilados) ──────────────────────────────
function Conhecimento({ push, d }) {
  const { Icon, Badge } = K;
  const [q, setQ] = useState('');
  const list = d.destilados.filter(x => window.matchQuery(q, x.title, x.summary, x.entities.join(' ')));
  return (
    <div>
      <LargeHeader title="Conhecimento" />
      <div style={{ padding: '0 16px 4px' }}><SearchField value={q} onChange={setQ} placeholder="Buscar destilados…" /></div>
      <SectionLabel>Destilados</SectionLabel>
      {list.length === 0 ? <Empty title="Nenhum destilado" description={`Nada casa com “${q}”.`} /> : (
        <div style={listWrap}>
          {list.map(dd => (
            <Card key={dd.id} onClick={() => push({ type: 'destilado', data: dd })} style={{ flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', gap: 12, width: '100%' }}>
                <Glyph name={SOURCE_ICON(dd.source)} icon={16} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                    <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)', flex: 1 }}>{dd.title}</span>
                    <span style={{ fontSize: 11, color: 'var(--muted-foreground)', flexShrink: 0 }}>{dd.date}</span>
                  </div>
                  <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.45, color: 'var(--muted-foreground)' }}>{dd.summary}</p>
                </div>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', width: '100%' }}>
                {dd.entities.map(e => <Badge key={e} variant="outline">{e}</Badge>)}
                <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--muted-foreground)' }}><Icon name={SOURCE_ICON(dd.source)} size={12} /> {dd.source}</span>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ── FLUXOS ─────────────────────────────────────────────────
function Fluxos({ push, d }) {
  const { Icon, Badge } = K;
  const [q, setQ] = useState('');
  const list = d.flows.filter(f => window.matchQuery(q, f.name, f.template, f.status));
  return (
    <div>
      <LargeHeader title="Fluxos" />
      <div style={{ padding: '0 16px 4px' }}><SearchField value={q} onChange={setQ} placeholder="Buscar fluxos…" /></div>
      <SectionLabel>Ativos</SectionLabel>
      {list.length === 0 ? <Empty title="Nenhum fluxo" description={`Nada casa com “${q}”.`} /> : (
        <div style={listWrap}>
          {list.map(f => {
            const pct = Math.round((f.budget.used / f.budget.limit) * 100);
            return (
              <Card key={f.id} onClick={() => push({ type: 'flow', data: f })} style={{ flexDirection: 'column', gap: 12 }}>
                <div style={{ display: 'flex', gap: 12, width: '100%', alignItems: 'flex-start' }}>
                  <Glyph name="workflow" icon={16} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{f.name}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)' }}><StatusDot status={f.status} /> {f.status}</span>
                      <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>· {f.template}</span>
                    </div>
                  </div>
                  {f.gate && <Badge variant="outline">gate</Badge>}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, width: '100%' }}>
                  <Cast list={f.cast} />
                  <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{f.tasksOpen} abertas</span>
                  <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, minWidth: 96 }}>
                    <div style={{ flex: 1, height: 4, borderRadius: 9999, background: 'var(--muted)', overflow: 'hidden' }}><div style={{ width: pct + '%', height: '100%', background: 'var(--foreground)' }} /></div>
                    <span style={{ fontSize: 11, color: 'var(--muted-foreground)', fontFamily: 'ui-monospace, monospace' }}>${f.budget.used}/{f.budget.limit}</span>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── EXECUÇÕES ──────────────────────────────────────────────
function RunRow({ r, onClick }) {
  const { Icon } = K;
  return (
    <Card onClick={onClick} style={{ alignItems: 'center', padding: 14 }}>
      <Glyph name={r.status === 'falhou' ? 'triangle-alert' : r.status === 'rodando' ? 'play' : 'circle-check'} size={30} icon={15} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)', fontFamily: 'ui-monospace, monospace' }}>{r.worker}</span>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--muted-foreground)', flexShrink: 0 }}>{r.started}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 3 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)' }}><StatusDot status={r.status} /> {r.status}</span>
          <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>· {r.duration} · {r.items} itens</span>
        </div>
      </div>
      <Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)' }} />
    </Card>
  );
}
function Execucoes({ push, d }) {
  const [q, setQ] = useState('');
  const list = d.runs.filter(r => window.matchQuery(q, r.worker, r.flow, r.status));
  return (
    <div>
      <LargeHeader title="Execuções" />
      <div style={{ padding: '0 16px 4px' }}><SearchField value={q} onChange={setQ} placeholder="Buscar por worker ou fluxo…" /></div>
      <SectionLabel>Cronológico</SectionLabel>
      {list.length === 0 ? <Empty title="Nenhuma execução" description={`Nada casa com “${q}”.`} /> : (
        <div style={listWrap}>{list.map(r => <RunRow key={r.id} r={r} onClick={() => push({ type: 'run', data: r })} />)}</div>
      )}
    </div>
  );
}

// ── MAIS ───────────────────────────────────────────────────
function Mais({ push, d, dark, onToggleDark }) {
  const { Icon, Switch } = K;
  const groups = [
    { label: 'Conhecimento', items: [
      { icon: 'network', name: 'Entidades', sub: `${d.entities.length} tipadas`, to: { type: 'entidades' } },
      { icon: 'rss', name: 'Fontes', sub: `${d.fontes.length} monitoradas`, to: { type: 'fontes' } },
    ] },
    { label: 'Orquestração', items: [
      { icon: 'user', name: 'Atores', sub: `${d.personas.length} personas`, to: { type: 'atores' } },
      { icon: 'blocks', name: 'Modelos', sub: `${d.templates.length} templates`, to: { type: 'modelos' } },
    ] },
    { label: 'Distribuição', items: [
      { icon: 'git-branch', name: 'Integrações', sub: `${d.integracoes.length} conectadas`, to: { type: 'integracoes' } },
      { icon: 'send', name: 'Envios', sub: `${d.envios.length} recentes`, to: { type: 'envios' } },
    ] },
  ];
  return (
    <div>
      <LargeHeader title="Mais" />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '4px 16px 8px', padding: '10px 16px', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
        <Glyph name="user" tone="primary" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{d.owner.name}</div>
          <div style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{d.owner.email}</div>
        </div>
      </div>
      {groups.map(g => (
        <div key={g.label}>
          <SectionLabel>{g.label}</SectionLabel>
          <div style={{ margin: '0 16px', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', overflow: 'hidden', background: 'var(--card)' }}>
            {g.items.map((it, i) => (
              <button key={it.name} onClick={() => push(it.to)} style={{ display: 'flex', alignItems: 'center', gap: 12, width: '100%', minHeight: 52, padding: '10px 14px', border: 'none', borderTop: i ? '1px solid var(--border)' : 'none', background: 'transparent', cursor: 'pointer', font: 'inherit', textAlign: 'left' }}>
                <Glyph name={it.icon} size={30} icon={15} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{it.name}</div>
                  <div style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{it.sub}</div>
                </div>
                <Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)' }} />
              </button>
            ))}
          </div>
        </div>
      ))}
      <SectionLabel>Aparência</SectionLabel>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 16px', padding: '12px 16px', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
        <Glyph name={dark ? 'moon' : 'sun'} size={30} icon={15} />
        <span style={{ flex: 1, fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>Tema escuro</span>
        {Switch ? <Switch checked={dark} onCheckedChange={onToggleDark} /> : <button onClick={() => onToggleDark(!dark)} style={{ padding: '6px 12px', border: '1px solid var(--border)', borderRadius: 9999, background: 'transparent', color: 'var(--foreground)', cursor: 'pointer', fontSize: 13 }}>{dark ? 'On' : 'Off'}</button>}
      </div>
    </div>
  );
}

// ── DETAIL VIEWS ───────────────────────────────────────────
function DestiladoDetail({ data: dd, push, d }) {
  const { Icon, Badge } = K;
  const prov = [
    { icon: 'book-open', kind: 'Destilado', label: dd.title, sub: dd.date },
    { icon: 'file', kind: 'Item bruto', label: dd.item, url: dd.itemUrl },
    { icon: 'rss', kind: 'Fonte', label: dd.source },
    { icon: 'activity', kind: 'Execução', label: dd.run, sub: 'run que produziu este destilado' },
  ];
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--foreground)' }}>{dd.title}</h2>
        <p style={{ margin: '6px 0 0', fontSize: 14, lineHeight: 1.5, color: 'var(--muted-foreground)' }}>{dd.summary}</p>
      </div>
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', padding: 16 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Claims extraídas</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
          {dd.claims.map((c, i) => <div key={i} style={{ display: 'flex', gap: 8, fontSize: 14, lineHeight: 1.45, color: 'var(--foreground)' }}><Icon name="circle-check" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 1 }} /> {c}</div>)}
        </div>
      </div>
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Entidades mencionadas</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {dd.entities.map(e => {
            const ent = d.entities.find(x => x.name === e);
            return <button key={e} onClick={() => ent && push({ type: 'entidade', data: ent })} disabled={!ent} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 9999, cursor: ent ? 'pointer' : 'default', font: 'inherit', fontSize: 12, color: 'var(--foreground)', background: 'transparent', boxShadow: '0 0 0 1px var(--border)' }}><Icon name={ent ? (TYPE_ICON[ent.type] || 'network') : 'network'} size={12} style={{ color: 'var(--muted-foreground)' }} /> {e}</button>;
          })}
        </div>
      </div>
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 14 }}><Icon name="git-branch" size={16} /><span style={{ fontFamily: 'var(--font-heading)', fontSize: 14, fontWeight: 600 }}>Cadeia de proveniência</span></div>
        {prov.map((p, i) => (
          <div key={i} style={{ display: 'flex', gap: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <Glyph name={p.icon} size={32} icon={16} />
              {i < prov.length - 1 && <div style={{ width: 2, flex: 1, minHeight: 14, background: 'var(--border)', margin: '4px 0' }} />}
            </div>
            <div style={{ paddingBottom: i < prov.length - 1 ? 14 : 0, minWidth: 0 }}>
              <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--muted-foreground)' }}>{p.kind}</span>
              <p style={{ margin: '2px 0 0', fontSize: 14, color: 'var(--foreground)' }}>{p.label}</p>
              {p.sub && <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>{p.sub}</p>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function EntidadeDetail({ data: e, push, d }) {
  const { Icon, Badge } = K;
  const mentions = d.destilados.filter(dd => dd.entities.includes(e.name));
  const icon = TYPE_ICON[e.type] || 'network';
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Glyph name={icon} size={46} icon={22} />
        <div>
          <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--foreground)' }}>{e.name}</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}><Badge variant="outline">{e.type}</Badge><span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{e.mentions} menções</span></div>
        </div>
      </div>
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Menções · 12 semanas</span>
          <Icon name="trending-up" size={16} style={{ color: 'var(--muted-foreground)' }} />
        </div>
        <window.MobileSparkline values={e.trend} />
      </div>
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Relações</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          {e.relations.map((r, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <span style={{ fontFamily: 'ui-monospace, monospace', color: 'var(--muted-foreground)' }}>{r.rel}</span>
              <Icon name="chevron-right" size={13} style={{ color: 'var(--muted-foreground)' }} />
              <span style={{ color: 'var(--foreground)' }}>{r.target}</span>
            </div>
          ))}
        </div>
      </div>
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Destilados que mencionam</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          {mentions.map(dd => (
            <Card key={dd.id} onClick={() => push({ type: 'destilado', data: dd })} style={{ padding: 12, alignItems: 'center' }}>
              <Icon name="book-open" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}><span style={{ fontSize: 13, fontWeight: 500, color: 'var(--foreground)' }}>{dd.title}</span><div style={{ fontSize: 11, color: 'var(--muted-foreground)', marginTop: 2 }}>{dd.source} · {dd.date}</div></div>
              <Icon name="chevron-right" size={14} style={{ color: 'var(--muted-foreground)' }} />
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}

function FlowDetail({ data: f, push }) {
  const { Icon, Badge } = K;
  const pct = Math.round((f.budget.used / f.budget.limit) * 100);
  const byState = {};
  f.states.forEach(s => byState[s] = f.tasks.filter(t => t.state === s));
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--foreground)' }}>{f.name}</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)' }}><StatusDot status={f.status} /> {f.status}</span>
          <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>· {f.template} · desde {f.created}</span>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <div style={{ flex: 1, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', padding: 14 }}>
          <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>Elenco</span>
          <div style={{ marginTop: 8 }}><Cast list={f.cast} size={28} /></div>
        </div>
        <div style={{ flex: 1, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', padding: 14 }}>
          <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>Budget</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
            <div style={{ flex: 1, height: 5, borderRadius: 9999, background: 'var(--muted)', overflow: 'hidden' }}><div style={{ width: pct + '%', height: '100%', background: 'var(--foreground)' }} /></div>
            <span style={{ fontSize: 12, fontFamily: 'ui-monospace, monospace', color: 'var(--foreground)' }}>${f.budget.used}/{f.budget.limit}</span>
          </div>
        </div>
      </div>
      {f.tasks.filter(t => t.gate).map(t => (
        <button key={t.id} onClick={() => push({ type: 'gate', flow: f, task: t })} style={{ display: 'flex', gap: 12, alignItems: 'center', width: '100%', padding: 16, textAlign: 'left', border: '1px solid color-mix(in oklab, var(--foreground) 24%, transparent)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', cursor: 'pointer', font: 'inherit' }}>
          <Glyph name="shield" tone="primary" />
          <div style={{ flex: 1, minWidth: 0 }}><div style={{ display: 'flex', gap: 6, marginBottom: 4 }}><Badge variant="outline">gate</Badge></div><span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{t.title}</span></div>
          <Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)' }} />
        </button>
      ))}
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Tarefas</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          {f.tasks.map(t => (
            <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', background: 'var(--card)' }}>
              <Cast list={[t.persona]} size={26} />
              <div style={{ flex: 1, minWidth: 0 }}><span style={{ fontSize: 13, color: 'var(--foreground)' }}>{t.title}</span>{t.blocked && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--destructive)' }}>bloqueada</span>}</div>
              <Badge variant="outline">{t.state.replace('_', ' ')}</Badge>
            </div>
          ))}
          {f.tasks.length === 0 && <p style={{ margin: 0, fontSize: 13, color: 'var(--muted-foreground)' }}>Sem tarefas ativas.</p>}
        </div>
      </div>
    </div>
  );
}

function GateDetail({ flow, task }) {
  const { Icon, Badge, Button } = K;
  const g = task.gateContext || { pede: task.title, produzido: [], budget: flow.budget.used + ' / ' + flow.budget.limit };
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Glyph name="shield" tone="primary" size={46} icon={22} />
        <div><div style={{ display: 'flex', gap: 6 }}><Badge variant="outline">gate</Badge></div><h2 style={{ margin: '4px 0 0', fontFamily: 'var(--font-heading)', fontSize: 18, fontWeight: 600, color: 'var(--foreground)' }}>{task.title}</h2></div>
      </div>
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', padding: 16 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>O que se pede</span>
        <p style={{ margin: '6px 0 0', fontSize: 14, lineHeight: 1.5, color: 'var(--foreground)' }}>{g.pede}</p>
      </div>
      {g.produzido.length > 0 && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', padding: 16 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Produzido até aqui</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
            {g.produzido.map((p, i) => <div key={i} style={{ display: 'flex', gap: 8, fontSize: 13, lineHeight: 1.45, color: 'var(--foreground)' }}><Icon name="circle-check" size={15} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 1 }} /> {p}</div>)}
          </div>
          <div style={{ display: 'flex', gap: 16, marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--muted-foreground)' }}>
            {g.pr && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="git-branch" size={13} /> {g.pr}</span>}
            {g.budget && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="zap" size={13} /> ${g.budget}</span>}
          </div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 10 }}>
        <div style={{ flex: 1 }}>{Button ? <Button style={{ width: '100%' }}>Aprovar</Button> : null}</div>
        <div style={{ flex: 1 }}>{Button ? <Button variant="outline" style={{ width: '100%' }}>Rejeitar</Button> : null}</div>
      </div>
    </div>
  );
}

function RunDetail({ data: r }) {
  const { Icon, Badge } = K;
  const rows = [['Worker', r.worker], ['Fluxo', r.flow], ['Início', r.started], ['Duração', r.duration], ['Itens', String(r.items)]];
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Glyph name={r.status === 'falhou' ? 'triangle-alert' : r.status === 'rodando' ? 'play' : 'circle-check'} size={46} icon={22} tone={r.status === 'falhou' ? 'muted' : 'muted'} />
        <div><h2 style={{ margin: 0, fontFamily: 'ui-monospace, monospace', fontSize: 18, fontWeight: 600, color: 'var(--foreground)' }}>{r.id}</h2><div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)', marginTop: 4 }}><StatusDot status={r.status} /> {r.status}</div></div>
      </div>
      {r.error && (
        <div style={{ border: '1px solid color-mix(in oklab, var(--destructive) 40%, var(--border))', borderRadius: 'var(--radius-xl)', background: 'color-mix(in oklab, var(--destructive) 8%, var(--card))', padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--destructive)', marginBottom: 6 }}><Icon name="triangle-alert" size={15} /><span style={{ fontSize: 12, fontWeight: 600 }}>Erro</span></div>
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: 'var(--foreground)', fontFamily: 'ui-monospace, monospace' }}>{r.error}</p>
        </div>
      )}
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', overflow: 'hidden' }}>
        {rows.map(([k, v], i) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 16px', borderTop: i ? '1px solid var(--border)' : 'none', fontSize: 14 }}>
            <span style={{ color: 'var(--muted-foreground)' }}>{k}</span>
            <span style={{ color: 'var(--foreground)', fontWeight: 500 }}>{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Mais sub-lists (generic) — searchable + navigable
function SimpleList({ view, d, push }) {
  const { Icon, Badge } = K;
  const [q, setQ] = useState('');
  const cfg = {
    atores: { title: 'Atores', ph: 'Buscar personas…' },
    modelos: { title: 'Modelos', ph: 'Buscar modelos…' },
    fontes: { title: 'Fontes', ph: 'Buscar fontes…' },
    entidades: { title: 'Entidades', ph: 'Buscar entidades…' },
    integracoes: { title: 'Integrações', ph: 'Buscar integrações…' },
    envios: { title: 'Envios', ph: 'Buscar envios…' },
  }[view.type];
  const M = window.matchQuery;
  let rows = [];
  if (view.type === 'atores') rows = d.personas.filter(p => M(q, p.name, p.executor, p.model, p.skills.join(' '))).map(p => (
    <Card key={p.id} onClick={() => push({ type: 'ator', data: p })}><Cast list={[p.emoji]} size={34} /><div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{p.name}</div><div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 2 }}>{p.executor} · {p.model}</div><div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>{p.skills.map(s => <Badge key={s} variant="outline">{s}</Badge>)}</div></div><Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)', alignSelf: 'center' }} /></Card>
  ));
  if (view.type === 'modelos') rows = d.templates.filter(t => M(q, t.name, t.trigger, t.states.join(' '))).map(t => (
    <Card key={t.id} onClick={() => push({ type: 'modelo', data: t })}><Glyph name="blocks" /><div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)', fontFamily: 'ui-monospace, monospace' }}>{t.name}</div><div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 2 }}>{t.trigger} · budget ${t.budget}</div><div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}><Cast list={t.cast} /><span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{t.states.length} estados</span></div></div><Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)', alignSelf: 'center' }} /></Card>
  ));
  if (view.type === 'fontes') rows = d.fontes.filter(s => M(q, s.name, s.type, s.health)).map(s => (
    <Card key={s.id} onClick={() => push({ type: 'fonte', data: s })}><Glyph name={s.type === 'youtube' ? 'youtube' : s.type === 'api' ? 'git-branch' : 'rss'} /><div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{s.name}</div><div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 2 }}>{s.items} itens · {s.last}</div></div><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)', alignSelf: 'center' }}><StatusDot status={s.health} /> {s.health}</span></Card>
  ));
  if (view.type === 'entidades') rows = d.entities.filter(e => M(q, e.name, e.type)).map(e => (
    <Card key={e.id} onClick={() => push({ type: 'entidade', data: e })}><Glyph name={TYPE_ICON[e.type] || 'network'} /><div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{e.name}</div><div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 2 }}>{e.mentions} menções · {e.type}</div></div><Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)', alignSelf: 'center' }} /></Card>
  ));
  if (view.type === 'integracoes') rows = d.integracoes.filter(it => M(q, it.name, it.status)).map(it => (
    <Card key={it.id} onClick={() => push({ type: 'integracao', data: it })}><Glyph name={it.icon} /><div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)', fontFamily: 'ui-monospace, monospace' }}>{it.name}</div><div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 2 }}>{it.rateLimit} · {it.secret}</div></div><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)', alignSelf: 'center' }}><StatusDot status={it.status} /> {it.status}</span></Card>
  ));
  if (view.type === 'envios') rows = d.envios.filter(s => M(q, s.kind, s.channel, s.to)).map(s => (
    <Card key={s.id}><Glyph name={s.channel === 'Telegram' ? 'send' : 'mail'} /><div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{s.kind}</div><div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 2 }}>{s.channel} → {s.to}</div></div><span style={{ fontSize: 11, color: 'var(--muted-foreground)', alignSelf: 'center' }}>{s.when}</span></Card>
  ));
  return (
    <div>
      <div style={{ padding: '0 16px 4px' }}><SearchField value={q} onChange={setQ} placeholder={cfg.ph} /></div>
      <SectionLabel>{cfg.title}</SectionLabel>
      {rows.length === 0 ? <Empty title="Nada encontrado" description={`Nada casa com “${q}”.`} /> : <div style={listWrap}>{rows}</div>}
    </div>
  );
}

// Detail: Ator (persona)
function AtorDetail({ data: p, d }) {
  const { Icon, Badge } = K;
  const flowsWith = d.flows.filter(f => f.cast.includes(p.emoji));
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 46, height: 46, borderRadius: 9999, background: 'var(--muted)', fontSize: 22 }}>{p.emoji}</span>
        <div><h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--foreground)' }}>{p.name}</h2><div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}><Badge variant="outline">{p.executor}</Badge><span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{p.model}</span></div></div>
      </div>
      <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Skills</span><div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>{p.skills.map(s => <Badge key={s} variant="outline">{s}</Badge>)}</div></div>
      <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Permissões</span><div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>{p.perms.map(pm => <div key={pm} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--foreground)', fontFamily: 'ui-monospace, monospace' }}><Icon name="circle-check" size={14} style={{ color: 'var(--muted-foreground)' }} /> {pm}</div>)}</div></div>
      {flowsWith.length > 0 && <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Atua em</span><div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>{flowsWith.map(f => <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', background: 'var(--card)' }}><Icon name="workflow" size={16} style={{ color: 'var(--muted-foreground)' }} /><span style={{ flex: 1, fontSize: 13, color: 'var(--foreground)' }}>{f.name}</span><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)' }}><StatusDot status={f.status} /> {f.status}</span></div>)}</div></div>}
    </div>
  );
}

// Detail: Modelo (template)
function ModeloDetail({ data: t, d }) {
  const { Icon, Badge } = K;
  const flowsOf = d.flows.filter(f => f.template === t.name);
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Glyph name="blocks" size={46} icon={22} />
        <div><h2 style={{ margin: 0, fontFamily: 'ui-monospace, monospace', fontSize: 19, fontWeight: 600, color: 'var(--foreground)' }}>{t.name}</h2><div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 4 }}>{t.trigger} · budget ${t.budget}</div></div>
      </div>
      <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Máquina de estados</span><div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 8 }}>{t.states.map((s, i) => <React.Fragment key={s}><span style={{ padding: '5px 10px', borderRadius: 9999, fontSize: 12, background: t.gates.includes(s) ? 'var(--primary)' : 'var(--muted)', color: t.gates.includes(s) ? 'var(--primary-foreground)' : 'var(--muted-foreground)' }}>{s}</span>{i < t.states.length - 1 && <Icon name="chevron-right" size={13} style={{ color: 'var(--muted-foreground)' }} />}</React.Fragment>)}</div></div>
      <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Elenco</span><div style={{ marginTop: 8 }}><Cast list={t.cast} size={30} /></div></div>
      {flowsOf.length > 0 && <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Fluxos deste modelo</span><div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>{flowsOf.map(f => <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', background: 'var(--card)' }}><Icon name="workflow" size={16} style={{ color: 'var(--muted-foreground)' }} /><span style={{ flex: 1, fontSize: 13, color: 'var(--foreground)' }}>{f.name}</span><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)' }}><StatusDot status={f.status} /> {f.status}</span></div>)}</div></div>}
    </div>
  );
}

// Detail: Fonte
function FonteDetail({ data: s, push, d }) {
  const { Icon, Badge } = K;
  const fromHere = d.destilados.filter(dd => dd.source.toLowerCase().includes(s.name.toLowerCase().split(' ')[0]));
  const rows = [['Tipo', s.type], ['Integração', s.integ], ['Última coleta', s.last], ['Itens', String(s.items)]];
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Glyph name={s.type === 'youtube' ? 'youtube' : s.type === 'api' ? 'git-branch' : 'rss'} size={46} icon={22} />
        <div><h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--foreground)' }}>{s.name}</h2><div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)', marginTop: 4 }}><StatusDot status={s.health} /> {s.health}</div></div>
      </div>
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', overflow: 'hidden' }}>{rows.map(([k, v], i) => <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 16px', borderTop: i ? '1px solid var(--border)' : 'none', fontSize: 14 }}><span style={{ color: 'var(--muted-foreground)' }}>{k}</span><span style={{ color: 'var(--foreground)', fontWeight: 500 }}>{v}</span></div>)}</div>
      {fromHere.length > 0 && <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Destilados desta fonte</span><div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>{fromHere.map(dd => <Card key={dd.id} onClick={() => push({ type: 'destilado', data: dd })} style={{ padding: 12, alignItems: 'center' }}><Icon name="book-open" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0 }} /><div style={{ flex: 1, minWidth: 0 }}><span style={{ fontSize: 13, fontWeight: 500, color: 'var(--foreground)' }}>{dd.title}</span><div style={{ fontSize: 11, color: 'var(--muted-foreground)', marginTop: 2 }}>{dd.date}</div></div><Icon name="chevron-right" size={14} style={{ color: 'var(--muted-foreground)' }} /></Card>)}</div></div>}
    </div>
  );
}

// Detail: Integração
function IntegracaoDetail({ data: it, d }) {
  const { Icon, Badge } = K;
  const srcs = d.fontes.filter(s => s.integ === it.name);
  const rows = [['Status', it.status], ['Rate limit', it.rateLimit], ['Segredo', it.secret]];
  return (
    <div style={{ padding: '4px 20px 8px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Glyph name={it.icon} size={46} icon={22} />
        <div><h2 style={{ margin: 0, fontFamily: 'ui-monospace, monospace', fontSize: 19, fontWeight: 600, color: 'var(--foreground)' }}>{it.name}</h2><div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)', marginTop: 4 }}><StatusDot status={it.status} /> {it.status}</div></div>
      </div>
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', overflow: 'hidden' }}>{rows.map(([k, v], i) => <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 16px', borderTop: i ? '1px solid var(--border)' : 'none', fontSize: 14 }}><span style={{ color: 'var(--muted-foreground)' }}>{k}</span><span style={{ color: 'var(--foreground)', fontWeight: 500 }}>{v}</span></div>)}</div>
      {srcs.length > 0 && <div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Fontes usando esta integração</span><div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>{srcs.map(s => <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', background: 'var(--card)' }}><Icon name="rss" size={15} style={{ color: 'var(--muted-foreground)' }} /><span style={{ flex: 1, fontSize: 13, color: 'var(--foreground)' }}>{s.name}</span><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--muted-foreground)' }}><StatusDot status={s.health} /> {s.health}</span></div>)}</div></div>}
    </div>
  );
}

const DETAIL_TITLES = { destilado: 'Destilado', entidade: 'Entidade', flow: 'Fluxo', gate: 'Gate', run: 'Execução', 'runs-all': 'Execuções', atores: 'Atores', modelos: 'Modelos', fontes: 'Fontes', entidades: 'Entidades', integracoes: 'Integrações', envios: 'Envios', ator: 'Ator', modelo: 'Modelo', fonte: 'Fonte', integracao: 'Integração' };

function DetailView({ view, push, d }) {
  switch (view.type) {
    case 'destilado': return <DestiladoDetail data={view.data} push={push} d={d} />;
    case 'entidade': return <EntidadeDetail data={view.data} push={push} d={d} />;
    case 'flow': return <FlowDetail data={view.data} push={push} />;
    case 'gate': return <GateDetail flow={view.flow} task={view.task} />;
    case 'run': return <RunDetail data={view.data} />;
    case 'ator': return <AtorDetail data={view.data} d={d} />;
    case 'modelo': return <ModeloDetail data={view.data} d={d} />;
    case 'fonte': return <FonteDetail data={view.data} push={push} d={d} />;
    case 'integracao': return <IntegracaoDetail data={view.data} d={d} />;
    case 'runs-all': return <div style={{ paddingTop: 8 }}>{[...d.runs].map(r => <div key={r.id} style={{ padding: '0 16px', marginBottom: 10 }}><RunRow r={r} onClick={() => push({ type: 'run', data: r })} /></div>)}</div>;
    default: return <SimpleList view={view} d={d} push={push} />;
  }
}

// ── ROOT ───────────────────────────────────────────────────
const TABS = [
  { key: 'painel', label: 'Painel', icon: 'house', screen: Painel },
  { key: 'conhecimento', label: 'Saber', icon: 'book-open', screen: Conhecimento },
  { key: 'fluxos', label: 'Fluxos', icon: 'workflow', screen: Fluxos },
  { key: 'execucoes', label: 'Execuções', icon: 'activity', screen: Execucoes },
  { key: 'mais', label: 'Mais', icon: 'ellipsis', screen: Mais },
];

function KuboMobileApp({ dark: darkProp = false }) {
  const [dark, setDark] = useState(darkProp);
  const [tab, setTab] = useState('painel');
  const [stacks, setStacks] = useState({ painel: [], conhecimento: [], fluxos: [], execucoes: [], mais: [] });
  const [ready, setReady] = useState(!!(window.KoboDesignSystem_6efae6 && window.KUBO_DATA));
  React.useEffect(() => {
    if (ready) return;
    const id = setInterval(() => { if (window.KoboDesignSystem_6efae6 && window.KUBO_DATA) { setReady(true); clearInterval(id); } }, 40);
    return () => clearInterval(id);
  }, [ready]);
  if (!ready) return <window.IOSDevice dark={dark}><div style={{ height: '100%' }} /></window.IOSDevice>;
  K = window.KoboDesignSystem_6efae6;
  const { Icon } = K;
  const d = window.KUBO_DATA;
  const stack = stacks[tab];
  const push = (view) => setStacks(s => ({ ...s, [tab]: [...s[tab], view] }));
  const pop = () => setStacks(s => ({ ...s, [tab]: s[tab].slice(0, -1) }));
  const TabScreen = TABS.find(t => t.key === tab).screen;
  const top = stack[stack.length - 1];

  return (
    <window.IOSDevice dark={dark}>
      <div className={dark ? 'dark' : ''} style={{ height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--background)', color: 'var(--foreground)', fontFamily: 'var(--font-sans)' }}>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <div style={{ height: 56 }} />
          {top && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 12px 6px' }}>
              <button onClick={pop} style={{ display: 'inline-flex', alignItems: 'center', gap: 2, minHeight: 44, padding: '0 8px', border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--foreground)', font: 'inherit', fontSize: 15 }}>
                <Icon name="chevron-right" size={18} style={{ transform: 'rotate(180deg)' }} /> {stack.length > 1 ? DETAIL_TITLES[stack[stack.length - 2].type] : TABS.find(t => t.key === tab).label}
              </button>
              <span style={{ marginLeft: 'auto', marginRight: 8, fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{DETAIL_TITLES[top.type]}</span>
            </div>
          )}
          {top ? <DetailView view={top} push={push} d={d} /> : (
            tab === 'mais'
              ? <Mais push={push} d={d} dark={dark} onToggleDark={setDark} />
              : <TabScreen push={push} d={d} />
          )}
          <div style={{ height: 96 }} />
        </div>

        {/* Bottom tab bar */}
        <div style={{ flexShrink: 0, display: 'flex', padding: '8px 8px 24px', borderTop: '1px solid color-mix(in oklab, var(--border) 60%, transparent)', background: 'color-mix(in oklab, var(--background) 88%, transparent)', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)' }}>
          {TABS.map(t => {
            const on = t.key === tab;
            return (
              <button key={t.key} onClick={() => setTab(t.key)} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, minHeight: 48, padding: '4px 0', border: 'none', background: 'transparent', cursor: 'pointer', color: on ? 'var(--foreground)' : 'var(--muted-foreground)', font: 'inherit' }}>
                <Icon name={t.icon} size={22} />
                <span style={{ fontSize: 10.5, fontWeight: on ? 600 : 500, letterSpacing: '-0.01em' }}>{t.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </window.IOSDevice>
  );
}

// Monochrome sparkline for the mobile entity detail.
function MobileSparkline({ values = [], width = 320, height = 56 }) {
  if (!values.length) return null;
  const max = Math.max(...values, 1), min = Math.min(...values, 0), span = max - min || 1;
  const stepX = width / (values.length - 1 || 1);
  const pts = values.map((v, i) => [i * stepX, height - ((v - min) / span) * (height - 6) - 3]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ display: 'block' }}>
      <path d={`${line} L${width} ${height} L0 ${height} Z`} fill="color-mix(in oklab, var(--foreground) 8%, transparent)" />
      <path d={line} fill="none" stroke="var(--foreground)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
window.MobileSparkline = MobileSparkline;
window.KuboMobileApp = KuboMobileApp;
