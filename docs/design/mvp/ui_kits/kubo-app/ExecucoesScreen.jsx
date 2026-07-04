// Execuções — tabela de runs com filtros e erros estruturados expansíveis.
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

function RunRow({ r }) {
  const { Badge, Icon } = K;
  const sv = window.KUBO_STATUS;
  const [open, setOpen] = useState(false);
  const td = { padding: '12px', fontSize: 14, color: 'var(--foreground)', borderBottom: '1px solid var(--border)', verticalAlign: 'middle' };
  return (
    <>
      <tr style={{ cursor: r.error ? 'pointer' : 'default' }} onClick={() => r.error && setOpen(o => !o)}>
        <td style={{ ...td, paddingLeft: 24, fontFamily: 'ui-monospace, monospace', fontWeight: 500 }}>{r.worker}</td>
        <td style={{ ...td, color: 'var(--muted-foreground)' }}>{r.flow}</td>
        <td style={{ ...td, color: 'var(--muted-foreground)' }}>{r.started}</td>
        <td style={{ ...td, color: 'var(--muted-foreground)' }}>{r.duration}</td>
        <td style={td}><Badge variant={sv(r.status)}>{r.status}</Badge></td>
        <td style={{ ...td, textAlign: 'right', color: 'var(--muted-foreground)' }}>{r.items}</td>
        <td style={{ ...td, paddingRight: 24, width: 32 }}>{r.error && <Icon name="chevron-down" size={16} style={{ color: 'var(--muted-foreground)', transform: open ? 'rotate(180deg)' : 'none' }} />}</td>
      </tr>
      {open && r.error && (
        <tr>
          <td colSpan={7} style={{ padding: '0 24px 12px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', gap: 8, padding: '10px 12px', borderRadius: 'var(--radius-lg)', background: 'color-mix(in oklab, var(--destructive) 10%, transparent)', color: 'var(--destructive)', fontSize: 13, fontFamily: 'ui-monospace, monospace' }}>
              <Icon name="triangle-alert" size={16} style={{ flexShrink: 0 }} /> {r.error}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ExecucoesScreen() {
  const { PageHeader, Card, CardContent, Select } = K;
  const d = window.KUBO_DATA;
  const th = { textAlign: 'left', padding: '10px 12px', fontSize: 12, fontWeight: 500, color: 'var(--muted-foreground)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Execuções" description="Runs dos seus workers — status, duração, itens produzidos e erros." />
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <Select options={['Todos os workers', 'yt-collector', 'rss-collector', 'distiller', 'digest-builder']} defaultValue="Todos os workers" size="sm" />
        <Select options={['Todos os status', 'concluída', 'rodando', 'falhou']} defaultValue="Todos os status" size="sm" />
        <Select options={['Últimos 7 dias', 'Hoje', 'Últimos 30 dias']} defaultValue="Últimos 7 dias" size="sm" />
      </div>
      <Card>
        <CardContent style={{ padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ ...th, paddingLeft: 24 }}>Worker</th>
                <th style={th}>Flow</th>
                <th style={th}>Início</th>
                <th style={th}>Duração</th>
                <th style={th}>Status</th>
                <th style={{ ...th, textAlign: 'right' }}>Itens</th>
                <th style={{ ...th, paddingRight: 24 }}></th>
              </tr>
            </thead>
            <tbody>
              {d.runs.map(r => <RunRow key={r.id} r={r} />)}
            </tbody>
          </table>
        </CardContent>
      </Card>
      <p style={{ margin: 0, fontSize: 12, color: 'var(--muted-foreground)' }}>Clique numa run com falha para ver a mensagem de erro estruturada.</p>
    </div>
  );
}
window.ExecucoesScreen = ExecucoesScreen;
