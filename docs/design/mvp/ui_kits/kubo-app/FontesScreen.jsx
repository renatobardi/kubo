// Fontes — de onde vem o que eu sei. Saúde da coleta por fonte.
const K = window.KoboDesignSystem_6efae6;

const TYPE_ICON = { youtube: 'youtube', rss: 'rss', site: 'network', api: 'cpu' };
const HEALTH_VARIANT = { ok: 'secondary', degradada: 'destructive', 'sem coleta': 'outline' };
const HEALTH_LABEL = { ok: 'ok', degradada: 'degradada', 'sem coleta': 'sem coleta há dias' };

function FontesScreen() {
  const { PageHeader, Card, CardHeader, CardTitle, CardDescription, CardContent, Badge, Button, Icon } = K;
  const d = window.KUBO_DATA;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Fontes" description="De onde vem o que você sabe — canais, feeds e sites, com a saúde de cada coleta."
        actions={<Button variant="outline" icon="plus">Adicionar fonte</Button>} />
      <Card>
        <CardHeader>
          <CardTitle>Fontes ativas</CardTitle>
          <CardDescription>{d.fontes.length} fontes · coletadas por workers do Operador.</CardDescription>
        </CardHeader>
        <CardContent style={{ padding: 0 }}>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {d.fontes.map((f, i) => (
              <li key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 24px', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}>
                  <Icon name={TYPE_ICON[f.type] || 'rss'} size={16} />
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{f.name}</span>
                    <Badge variant="outline">{f.type}</Badge>
                  </div>
                  <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>
                    via <span style={{ fontFamily: 'ui-monospace, monospace' }}>{f.integ}</span> · última coleta {f.last} · {f.items} itens acumulados
                  </p>
                </div>
                <Badge variant={HEALTH_VARIANT[f.health]}>{HEALTH_LABEL[f.health]}</Badge>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
window.FontesScreen = FontesScreen;
