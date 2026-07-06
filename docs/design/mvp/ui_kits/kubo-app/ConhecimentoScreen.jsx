// Conhecimento — grafo consultável de destilados com proveniência + entidades navegáveis.
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

const TYPE_ICON = { pessoa: 'user', tecnologia: 'cpu', 'organização': 'building-2', conceito: 'lightbulb' };
const SOURCE_ICON = (src) => {
  const s = (src || '').toLowerCase();
  if (s.includes('youtube')) return 'youtube';
  if (s.includes('github')) return 'git-branch';
  if (s.includes('post') || s.includes('blog')) return 'file';
  return 'rss';
};

function ProvenanceStep({ icon, kind, label, sub, url, last }) {
  const { Icon } = K;
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, flexShrink: 0, borderRadius: 'var(--radius-md)', background: 'var(--muted)', color: 'var(--foreground)' }}>
          <Icon name={icon} size={16} />
        </div>
        {!last && <div style={{ width: 2, flex: 1, minHeight: 16, background: 'var(--border)', margin: '4px 0' }} />}
      </div>
      <div style={{ paddingBottom: last ? 0 : 16, minWidth: 0 }}>
        <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--muted-foreground)' }}>{kind}</span>
        <p style={{ margin: '2px 0 0', fontSize: 14, color: 'var(--foreground)' }}>{label}</p>
        {sub && <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>{sub}</p>}
        {url && <a href={url} onClick={e => e.preventDefault()} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 4, fontSize: 12, color: 'var(--primary)', textDecoration: 'none' }}><Icon name="link" size={12} /> {url}</a>}
      </div>
    </div>
  );
}

function BackLink({ label, onClick }) {
  const { Icon } = K;
  return (
    <button onClick={onClick} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, alignSelf: 'flex-start', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 13, color: 'var(--muted-foreground)', fontFamily: 'var(--font-sans)' }}>
      <Icon name="chevron-right" size={14} style={{ transform: 'rotate(180deg)' }} /> {label}
    </button>
  );
}

function EntityChip({ name, onClick }) {
  const { Icon } = K;
  const ent = window.KUBO_DATA.entities.find(e => e.name === name);
  const icon = ent ? (TYPE_ICON[ent.type] || 'network') : 'network';
  return (
    <button onClick={() => onClick && onClick(name)} disabled={!onClick}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 9999, cursor: onClick ? 'pointer' : 'default', fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--foreground)', background: 'transparent', boxShadow: '0 0 0 1px var(--border)' }}
      onMouseEnter={e => { if (onClick) e.currentTarget.style.background = 'var(--muted)'; }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}>
      <Icon name={icon} size={12} style={{ color: 'var(--muted-foreground)' }} /> {name}
    </button>
  );
}

function DestiladoDetail({ d, onBack, onOpenEntity, onOpenDestilado }) {
  const { Card, CardContent, Icon } = K;
  const all = window.KUBO_DATA.destilados;
  const related = all.filter(x => x.id !== d.id && x.entities.some(e => d.entities.includes(e)));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <BackLink label="Voltar aos destilados" onClick={onBack} />
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.025em', color: 'var(--foreground)' }}>{d.title}</h2>
            <p style={{ margin: '6px 0 0', fontSize: 14, color: 'var(--muted-foreground)' }}>{d.summary}</p>
          </div>
          <Card>
            <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Claims extraídas</span>
              {d.claims.map((c, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, fontSize: 14, color: 'var(--foreground)' }}>
                  <Icon name="circle-check" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 1 }} /> {c}
                </div>
              ))}
            </CardContent>
          </Card>
          <div>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Entidades mencionadas</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
              {d.entities.map(e => <EntityChip key={e} name={e} onClick={onOpenEntity} />)}
            </div>
          </div>

          {/* #3 — Relacionados */}
          {related.length > 0 && (
            <div>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Relacionados</span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
                {related.map(r => {
                  const shared = r.entities.filter(e => d.entities.includes(e));
                  return (
                    <button key={r.id} onClick={() => onOpenDestilado(r)} style={{ textAlign: 'left', cursor: 'pointer', display: 'flex', gap: 10, padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', background: 'var(--card)' }}>
                      <Icon name="book-open" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 1 }} />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--foreground)' }}>{r.title}</span>
                        <div style={{ marginTop: 4, fontSize: 11, color: 'var(--muted-foreground)' }}>compartilha: {shared.join(', ')}</div>
                      </div>
                      <Icon name="chevron-right" size={14} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 2 }} />
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Cadeia de proveniência */}
        <Card>
          <CardContent>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 14 }}>
              <Icon name="git-branch" size={16} style={{ color: 'var(--foreground)' }} />
              <span style={{ fontFamily: 'var(--font-heading)', fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>Cadeia de proveniência</span>
            </div>
            <ProvenanceStep icon="book-open" kind="Destilado" label={d.title} sub={d.date} />
            <ProvenanceStep icon="file" kind="Item bruto" label={d.item} url={d.itemUrl} />
            <ProvenanceStep icon="rss" kind="Fonte" label={d.source} />
            <ProvenanceStep icon="activity" kind="Execução" label={d.run} sub="run que produziu este destilado" last />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// #4 — Detalhe da entidade
function EntityDetail({ entity, onBack, onOpenDestilado, onOpenEntity }) {
  const { Card, CardContent, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  const mentions = d.destilados.filter(dd => dd.entities.includes(entity.name));
  const icon = TYPE_ICON[entity.type] || 'network';
  const last = entity.trend[entity.trend.length - 1], first = entity.trend[0];
  const up = last >= first;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <BackLink label="Voltar às entidades" onClick={onBack} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 44, height: 44, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--foreground)' }}>
          <Icon name={icon} size={22} />
        </div>
        <div>
          <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: '-0.025em', color: 'var(--foreground)' }}>{entity.name}</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
            <Badge variant="outline">{entity.type}</Badge>
            <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{entity.mentions} menções · {mentions.length} destilados</span>
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'start' }}>
        {/* Sparkline mono */}
        <Card>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Menções ao longo do tempo</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--muted-foreground)' }}>
                <Icon name="trending-up" size={14} style={{ transform: up ? 'none' : 'scaleY(-1)' }} /> últimas 12 semanas
              </span>
            </div>
            <window.Sparkline values={entity.trend} width={280} height={56} />
          </CardContent>
        </Card>

        {/* Relações tipadas */}
        <Card>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Relações</span>
            {entity.relations.map((r, i) => {
              const known = d.entities.some(e => e.name === r.target);
              return (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26, borderRadius: 9999, background: 'var(--muted)', color: 'var(--muted-foreground)' }}><Icon name={icon} size={13} /></span>
                  <span style={{ fontFamily: 'ui-monospace, monospace', color: 'var(--muted-foreground)' }}>{r.rel}</span>
                  <Icon name="chevron-right" size={13} style={{ color: 'var(--muted-foreground)' }} />
                  {known ? <EntityChip name={r.target} onClick={onOpenEntity} /> : <span style={{ color: 'var(--foreground)' }}>{r.target}</span>}
                </div>
              );
            })}
          </CardContent>
        </Card>
      </div>

      {/* Destilados que mencionam */}
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)' }}>Destilados que mencionam</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          {mentions.map(dd => (
            <button key={dd.id} onClick={() => onOpenDestilado(dd)} style={{ textAlign: 'left', cursor: 'pointer', display: 'flex', gap: 10, padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', background: 'var(--card)' }}>
              <Icon name="book-open" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 1 }} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--foreground)' }}>{dd.title}</span>
                <div style={{ marginTop: 2, fontSize: 11, color: 'var(--muted-foreground)' }}>{dd.source} · {dd.date}</div>
              </div>
              <Icon name="chevron-right" size={14} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 2 }} />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function EntitiesView({ onOpen }) {
  const { Card, CardContent, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');
  const filtered = d.entities.filter(e => window.matchQuery(query, e.name, e.type));
  const ring = '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)';
  const body = (e) => (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}><Icon name={TYPE_ICON[e.type] || 'network'} size={16} /></div>
        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)', flex: 1 }}>{e.name}</span>
        <window.Sparkline values={e.trend} width={64} height={20} fill={false} stroke="var(--muted-foreground)" />
        <Badge variant="outline">{e.type}</Badge>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{e.mentions} menções</span>
        {e.relations.map((r, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--muted-foreground)' }}>
            <span style={{ fontFamily: 'ui-monospace, monospace', color: 'var(--foreground)' }}>{r.rel}</span>
            <Icon name="chevron-right" size={12} /> {r.target}
          </span>
        ))}
      </div>
    </>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <window.SearchBar value={query} onChange={setQuery} placeholder="Buscar entidades por nome ou tipo…" />
        <window.ViewToggle value={view} onChange={setView} allowed={['list', 'grid2']} />
      </div>
      {filtered.length === 0 ? (
        <window.EmptyState icon="search" title="Nenhuma entidade encontrada" description={`Nada casa com “${query}”. Tente outro termo ou limpe a busca.`} />
      ) : view === 'grid2' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
          {filtered.map(e => (
            <button key={e.id} onClick={() => onOpen(e)} style={{ textAlign: 'left', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 8, height: 108, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring, overflow: 'hidden' }}>
              {body(e)}
            </button>
          ))}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(e => (
            <button key={e.id} onClick={() => onOpen(e)} style={{ textAlign: 'left', cursor: 'pointer', border: 'none', background: 'transparent', padding: 0 }}>
              <Card size="sm">
                <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {body(e)}
                </CardContent>
              </Card>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ConhecimentoScreen({ initialTab } = {}) {
  const { PageHeader, Input, Icon, Badge } = K;
  const d = window.KUBO_DATA;
  const [tab, setTab] = useState(initialTab || 'Destilados');
  const [selected, setSelected] = useState(null);
  const [entity, setEntity] = useState(null);
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');

  const openEntity = (name) => {
    const e = typeof name === 'string' ? d.entities.find(x => x.name === name) : name;
    if (e) { setSelected(null); setEntity(e); setTab('Entidades'); }
  };
  const openDestilado = (dd) => { setEntity(null); setSelected(dd); setTab('Destilados'); };

  if (selected) return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <DestiladoDetail d={selected} onBack={() => setSelected(null)} onOpenEntity={openEntity} onOpenDestilado={openDestilado} />
    </div>
  );
  if (entity) return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <EntityDetail entity={entity} onBack={() => setEntity(null)} onOpenDestilado={openDestilado} onOpenEntity={openEntity} />
    </div>
  );

  const filtered = d.destilados.filter(dd => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return dd.title.toLowerCase().includes(q) || dd.summary.toLowerCase().includes(q) || dd.entities.some(e => e.toLowerCase().includes(q));
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title={tab === 'Entidades' ? 'Entidades' : 'Destilados'} description={tab === 'Entidades' ? 'Entidades tipadas extraídas dos destilados, com menções e relações.' : 'Grafo consultável de destilados, com citação de origem em cada nó.'} />

      {tab === 'Destilados' ? (
        <>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div style={{ position: 'relative', flex: '1 1 auto', maxWidth: 420 }}>
              <span style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted-foreground)', pointerEvents: 'none' }}><Icon name="search" size={16} /></span>
              <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Busca por texto ou semântica…" style={{ paddingLeft: 34 }} />
            </div>
            <window.ViewToggle value={view} onChange={setView} allowed={['list']} />
          </div>
          {filtered.length === 0 ? (
            <window.EmptyState icon="search" title="Nenhum destilado encontrado"
              description={`Nada casa com “${query}”. Tente outro termo ou limpe a busca.`} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {filtered.map(dd => (
                <button key={dd.id} onClick={() => setSelected(dd)} style={{ textAlign: 'left', cursor: 'pointer', display: 'flex', gap: 12, padding: 16, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}>
                    <Icon name={SOURCE_ICON(dd.source)} size={16} />
                  </div>
                  <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--foreground)', flex: 1 }}>{dd.title}</span>
                      <span style={{ fontSize: 12, color: 'var(--muted-foreground)', flexShrink: 0 }}>{dd.date}</span>
                    </div>
                    <p style={{ margin: 0, fontSize: 13, color: 'var(--muted-foreground)' }}>{dd.summary}</p>
                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
                      {dd.entities.map(e => <Badge key={e} variant="outline">{e}</Badge>)}
                      <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--muted-foreground)' }}><Icon name={SOURCE_ICON(dd.source)} size={12} /> {dd.source}</span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </>
      ) : <EntitiesView onOpen={openEntity} />}
    </div>
  );
}
window.ConhecimentoScreen = ConhecimentoScreen;
