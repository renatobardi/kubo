// Distribuição — Destinos (artefatos + destinos) e Envios (histórico).
// Canais de status moram em Catálogos > Integrações.
const K = window.KoboDesignSystem_6efae6;

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
  const { PageHeader, Card, CardHeader, CardTitle, CardDescription, CardContent, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Envios" description="Histórico do que já saiu — artefato, canal, destino e quando." />
      <Card>
        <CardHeader><CardTitle>Histórico de envios</CardTitle><CardDescription>Digests e relatórios entregues.</CardDescription></CardHeader>
        <CardContent style={{ padding: 0 }}>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {d.envios.map((s, i) => (
              <li key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 24px', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                <Icon name="send" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0 }} />
                <span style={{ fontSize: 14, color: 'var(--foreground)', flex: 1 }}>{s.kind}</span>
                <Badge variant="outline">{s.channel}</Badge>
                <span style={{ fontSize: 13, color: 'var(--muted-foreground)', minWidth: 110 }}>{s.to}</span>
                <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{s.when}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
window.EnviosScreen = EnviosScreen;
