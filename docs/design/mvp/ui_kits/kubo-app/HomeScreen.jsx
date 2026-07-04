// Home — visão geral do ateliê. Stats + gate alert + últimas execuções + flows ativos.
const K = window.KoboDesignSystem_6efae6;

window.KUBO_STATUS = function (s) {
  const done = ['concluída', 'stored', 'done', 'promoted', 'sent', 'ativo', 'conectada'];
  const running = ['rodando', 'collecting', 'distilling', 'in_progress'];
  const fail = ['falhou', 'failed', 'degradada'];
  if (done.includes(s)) return 'secondary';
  if (running.includes(s)) return 'default';
  if (fail.includes(s)) return 'destructive';
  return 'outline';
};

function HomeScreen({ onNavigate }) {
  const { PageHeader, StatTile, Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction, Badge, Button, Icon } = K;
  const d = window.KUBO_DATA;
  const sv = window.KUBO_STATUS;
  const gateFlows = d.flows.filter(f => f.gate);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Home" description="Seu ateliê de agentes — coleta, conhecimento e distribuição num relance." />

      {/* Gate alert */}
      {gateFlows.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderRadius: 'var(--radius-xl)',
          background: 'color-mix(in oklab, var(--primary) 8%, transparent)', boxShadow: '0 0 0 1px color-mix(in oklab, var(--primary) 25%, transparent)' }}>
          <Icon name="triangle-alert" size={16} style={{ color: 'var(--primary)' }} />
          <span style={{ fontSize: 14, color: 'var(--foreground)', flex: 1 }}>
            <strong style={{ fontWeight: 600 }}>1 decisão aguardando você</strong> — {gateFlows[0].name} tem um gate aberto.
          </span>
          <Button size="sm" variant="outline" onClick={() => onNavigate('Flows')}>Revisar</Button>
        </div>
      )}

      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <StatTile label="Fontes ativas" value={d.stats.fontesAtivas} icon="rss" onClick={() => onNavigate('Fontes')} />
        <StatTile label="Itens coletados (7d)" value={d.stats.itensColetados7d} icon="database" onClick={() => onNavigate('Execuções')} />
        <StatTile label="Destilados" value={d.stats.destilados} icon="book-open" onClick={() => onNavigate('Conhecimento')} />
        <StatTile label="Entidades no grafo" value={d.stats.entidades} icon="network" onClick={() => onNavigate('Conhecimento')} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'start' }}>
        {/* Últimas execuções */}
        <Card>
          <CardHeader>
            <CardTitle>Últimas execuções</CardTitle>
            <CardDescription>Runs recentes dos seus workers.</CardDescription>
            <CardAction><Button variant="ghost" size="sm" onClick={() => onNavigate('Execuções')}>Ver todas</Button></CardAction>
          </CardHeader>
          <CardContent style={{ padding: 0 }}>
            <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {d.runs.slice(0, 5).map((r, i) => (
                <li key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 24px', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, flexShrink: 0, borderRadius: 'var(--radius-md)', background: 'var(--muted)' }}>
                    <Icon name="activity" size={16} style={{ color: 'var(--muted-foreground)' }} />
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 14, fontWeight: 500, fontFamily: 'ui-monospace, monospace', color: 'var(--foreground)' }}>{r.worker}</span>
                      <Badge variant={sv(r.status)}>{r.status}</Badge>
                    </div>
                    <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>{r.started} · {r.duration} · {r.items} itens</p>
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        {/* Flows ativos */}
        <Card>
          <CardHeader>
            <CardTitle>Flows ativos</CardTitle>
            <CardDescription>Automações em andamento.</CardDescription>
            <CardAction><Button variant="ghost" size="sm" onClick={() => onNavigate('Flows')}>Ver todos</Button></CardAction>
          </CardHeader>
          <CardContent>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {d.flows.filter(f => f.status !== 'pausado').map(f => (
                <button key={f.id} onClick={() => onNavigate('Flows')} style={{ textAlign: 'left', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 8, padding: 12, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)', flex: 1 }}>{f.name}</span>
                    {f.gate && <Badge icon="triangle-alert">gate</Badge>}
                    <Badge variant={sv(f.status)}>{f.status}</Badge>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: 'var(--muted-foreground)' }}>
                    <span style={{ fontFamily: 'ui-monospace, monospace' }}>{f.template}</span>
                    <span style={{ display: 'flex', gap: 3 }}>{f.cast.map((e, i) => <window.PersonaGlyph key={i} glyph={e} size={20} />)}</span>
                    <span style={{ marginLeft: 'auto' }}>{f.tasksOpen} tasks abertas</span>
                  </div>
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
window.HomeScreen = HomeScreen;
