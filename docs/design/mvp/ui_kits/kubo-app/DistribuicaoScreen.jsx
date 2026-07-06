// Distribuição — Destinos (artefatos + destinos) e Envios (histórico).
// Canais de status moram em Catálogos > Integrações.
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

function DestinosScreen() {
  const { PageHeader, Card, CardHeader, CardTitle, CardDescription, CardContent, Badge, Button, Icon } = K;
  const d = window.KUBO_DATA;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Destinos" description="O que é distribuído e para onde. Convidados recebem — não operam."
        actions={<Button icon="plus">Novo artefato</Button>} />

      {/* Artefatos configurados */}
      <Card>
        <CardHeader><CardTitle>Artefatos configurados</CardTitle><CardDescription>Digests e relatórios recorrentes.</CardDescription></CardHeader>
        <CardContent style={{ padding: 0 }}>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {d.artefatos.map((a, i) => (
              <li key={a.id} style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '14px 24px', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{a.name}</span>
                  <Badge variant="outline" icon="clock">{a.agenda}</Badge>
                </div>
                <p style={{ margin: 0, fontSize: 12, color: 'var(--muted-foreground)' }}>
                  origem: <span style={{ fontFamily: 'ui-monospace, monospace' }}>{a.query}</span>
                </p>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>destinos:</span>
                  {a.destinos.map(dn => <Badge key={dn} variant="secondary">{dn}</Badge>)}
                </div>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      {/* Destinos */}
      <Card>
        <CardHeader><CardTitle>Destinos</CardTitle><CardDescription>Pessoas (dono + convidados) e sistemas.</CardDescription></CardHeader>
        <CardContent style={{ padding: 0 }}>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {d.destinos.map((r, i) => (
              <li key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 24px', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, flexShrink: 0, borderRadius: r.kind === 'pessoa' ? 9999 : 'var(--radius-md)', background: r.kind === 'pessoa' ? 'var(--primary)' : 'var(--muted)', color: r.kind === 'pessoa' ? 'var(--primary-foreground)' : 'var(--muted-foreground)', fontSize: 12, fontWeight: 600 }}>
                  {r.kind === 'pessoa' ? r.name.charAt(0) : <Icon name={r.sys === 'webhook' ? 'link' : 'database'} size={15} />}
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{r.name}</span>
                    {r.kind === 'pessoa'
                      ? <Badge variant={r.role === 'dono' ? 'default' : 'secondary'}>{r.role}</Badge>
                      : <Badge variant="outline">{r.sys}</Badge>}
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--muted-foreground)', background: 'var(--muted)', borderRadius: 9999, padding: '2px 8px' }}>
                      <Icon name="send" size={11} /> {r.channel}
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
window.DestinosScreen = DestinosScreen;

function EnviosScreen() {
  const { PageHeader, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  const [query, setQuery] = useState('');
  const [view, setView] = useState('list');
  const filtered = d.envios.filter(s => window.matchQuery(query, s.kind, s.channel, s.to));
  const ring = '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Envios" description="Histórico do que já saiu — artefato, canal, destino e quando." />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <window.SearchBar value={query} onChange={setQuery} placeholder="Buscar envios por artefato, canal ou destino…" />
        <window.ViewToggle value={view} onChange={setView} allowed={['list', 'grid2']} />
      </div>
      {filtered.length === 0 ? (
        <window.EmptyState icon="search" title="Nenhum envio encontrado"
          description={`Nada casa com “${query}”. Tente outro termo ou limpe a busca.`} />
      ) : view === 'grid2' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
          {filtered.map(s => (
            <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 12, height: 84, padding: 16, boxSizing: 'border-box', background: 'var(--card)', borderRadius: 'var(--radius-2xl)', boxShadow: ring }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}><Icon name="send" size={16} /></div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 14, color: 'var(--foreground)' }}>{s.kind}</span>
                  <Badge variant="outline">{s.channel}</Badge>
                </div>
                <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>{s.to} · {s.when}</p>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(s => (
            <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 16, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}><Icon name="send" size={16} /></div>
              <span style={{ fontSize: 14, color: 'var(--foreground)', flex: 1 }}>{s.kind}</span>
              <Badge variant="outline">{s.channel}</Badge>
              <span style={{ fontSize: 13, color: 'var(--muted-foreground)', minWidth: 110 }}>{s.to}</span>
              <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{s.when}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
window.EnviosScreen = EnviosScreen;
