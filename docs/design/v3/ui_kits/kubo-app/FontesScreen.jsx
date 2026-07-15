// Fontes — de onde vem o que eu sei. Saúde da coleta por fonte.
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

const TYPE_ICON = { youtube: 'youtube', rss: 'rss', site: 'network', api: 'cpu' };
const HEALTH_VARIANT = { ok: 'secondary', degradada: 'destructive', 'sem coleta': 'outline' };
const HEALTH_LABEL = { ok: 'ok', degradada: 'degradada', 'sem coleta': 'sem coleta há dias' };

function FontesScreen() {
  const { PageHeader, Badge, Button, Icon } = K;
  const d = window.KUBO_DATA;
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');
  const filtered = d.fontes.filter(f => window.matchQuery(query, f.name, f.type, f.integ, f.health));
  const ring = '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)';
  const typeIcon = (f) => (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}>
      <Icon name={TYPE_ICON[f.type] || 'rss'} size={16} />
    </div>
  );
  const health = (f) => <Badge variant={HEALTH_VARIANT[f.health]}>{HEALTH_LABEL[f.health]}</Badge>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Fontes" description="De onde vem o que você sabe — canais, feeds e sites, com a saúde de cada coleta."
        actions={<Button variant="outline" icon="plus">Adicionar fonte</Button>} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <window.SearchBar value={query} onChange={setQuery} placeholder="Buscar fontes por nome, tipo ou integração…" />
        <window.ViewToggle value={view} onChange={setView} allowed={['list', 'grid2', 'squares']} />
      </div>
      {filtered.length === 0 ? (
        <window.EmptyState icon="search" title="Nenhuma fonte encontrada"
          description={`Nada casa com “${query}”. Tente outro termo ou limpe a busca.`} />
      ) : view === 'squares' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
          {filtered.map(f => (
            <div key={f.id} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: 8, height: 176, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring }}>
              {typeIcon(f)}
              <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{f.name}</span>
              <Badge variant="outline">{f.type}</Badge>
              <span style={{ fontSize: 11, color: 'var(--muted-foreground)' }}>{f.items} itens</span>
              <div style={{ marginTop: 'auto' }}>{health(f)}</div>
            </div>
          ))}
        </div>
      ) : view === 'grid2' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
          {filtered.map(f => (
            <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 12, height: 84, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring }}>
              {typeIcon(f)}
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{f.name}</span>
                  <Badge variant="outline">{f.type}</Badge>
                </div>
                <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>via {f.integ} · {f.items} itens</p>
              </div>
              {health(f)}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(f => (
            <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 16, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
              {typeIcon(f)}
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{f.name}</span>
                  <Badge variant="outline">{f.type}</Badge>
                </div>
                <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>
                  via <span>{f.integ}</span> · última coleta {f.last} · {f.items} itens acumulados
                </p>
              </div>
              {health(f)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
window.FontesScreen = FontesScreen;
