// Execuções — lista de runs com busca e erros estruturados expansíveis.
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

function RunRow({ r }) {
  const { Badge, Icon } = K;
  const sv = window.KUBO_STATUS;
  const [open, setOpen] = useState(false);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)', overflow: 'hidden' }}>
      <div onClick={() => r.error && setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 16, cursor: r.error ? 'pointer' : 'default' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}>
          <Icon name="activity" size={16} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{r.worker}</span>
            <Badge variant={sv(r.status)}>{r.status}</Badge>
          </div>
          <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>{r.flow} · {r.started} · {r.duration} · {r.items} itens</p>
        </div>
        {r.error && <Icon name="chevron-down" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0, transform: open ? 'rotate(180deg)' : 'none' }} />}
      </div>
      {open && r.error && (
        <div style={{ padding: '0 16px 16px' }}>
          <div style={{ display: 'flex', gap: 8, padding: '10px 12px', borderRadius: 'var(--radius-lg)', background: 'color-mix(in oklab, var(--destructive) 10%, transparent)', color: 'var(--destructive)', fontSize: 13, fontFamily: 'ui-monospace, monospace' }}>
            <Icon name="triangle-alert" size={16} style={{ flexShrink: 0 }} /> {r.error}
          </div>
        </div>
      )}
    </div>
  );
}

function ExecucoesScreen() {
  const { PageHeader } = K;
  const d = window.KUBO_DATA;
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');
  const filtered = d.runs.filter(r => window.matchQuery(query, r.worker, r.flow, r.status));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Execuções" description="Runs dos seus workers — status, duração, itens produzidos e erros." />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <window.SearchBar value={query} onChange={setQuery} placeholder="Buscar runs por worker, fluxo ou status…" />
        <window.ViewToggle value={view} onChange={setView} allowed={['list']} />
      </div>
      {filtered.length === 0 ? (
        <window.EmptyState icon="search" title="Nenhuma run encontrada"
          description={`Nada casa com “${query}”. Tente outro termo ou limpe a busca.`} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(r => <RunRow key={r.id} r={r} />)}
        </div>
      )}
      <p style={{ margin: 0, fontSize: 12, color: 'var(--muted-foreground)' }}>Clique numa run com falha para ver a mensagem de erro estruturada.</p>
    </div>
  );
}
window.ExecucoesScreen = ExecucoesScreen;
