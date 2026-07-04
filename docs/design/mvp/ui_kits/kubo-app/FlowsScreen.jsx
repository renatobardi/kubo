// Flows — lista de flows + DETALHE como board kanban (colunas = estados do template).
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

const STATE_LABEL = {
  backlog: 'Backlog', analysis: 'Analysis', in_progress: 'In progress', review: 'Review', done: 'Done', promoted: 'Promoted',
  queued: 'Queued', collecting: 'Collecting', distilling: 'Distilling', stored: 'Stored', failed: 'Failed', sent: 'Sent',
};

function TaskCard({ t, onGate }) {
  const { Badge, Button, Icon } = K;
  const isGate = t.gate;
  return (
    <div style={{
      background: 'var(--card)', borderRadius: 'var(--radius-xl)', padding: 12,
      boxShadow: isGate
        ? '0 0 0 1.5px color-mix(in oklab, var(--primary) 55%, transparent)'
        : '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)',
    }}>
      {isGate && (
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginBottom: 8, fontSize: 11, fontWeight: 600, color: 'var(--primary)' }}>
          <Icon name="triangle-alert" size={12} /> aguardando você
        </div>
      )}
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.4, color: 'var(--foreground)' }}>{t.title}</p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10 }}>
        <window.PersonaGlyph glyph={t.persona} size={22} title={t.personaName} />
        <span style={{ fontSize: 12, color: 'var(--muted-foreground)', flex: 1 }}>{t.personaName}</span>
        {t.blocked && <Badge variant="outline" icon="link">bloqueada</Badge>}
        {t.error && <Badge variant="destructive">falhou</Badge>}
      </div>
      {isGate && (
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <Button size="xs" style={{ flex: 1 }} onClick={() => onGate(t)}>Aprovar</Button>
          <Button size="xs" variant="destructive" style={{ flex: 1 }} onClick={() => onGate(t)}>Rejeitar</Button>
        </div>
      )}
      {t.error && <p style={{ margin: '8px 0 0', fontSize: 11, color: 'var(--destructive)' }}>{t.error}</p>}
    </div>
  );
}

function GateSheet({ task, onClose }) {
  const { Button, Badge, Textarea, Icon } = K;
  const [reason, setReason] = useState('');
  const [rejecting, setRejecting] = useState(false);
  const c = task.gateContext || {};
  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 50, display: 'flex', justifyContent: 'flex-end' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'color-mix(in oklab, black 40%, transparent)' }} />
      <div style={{ position: 'relative', width: 440, maxWidth: '92vw', height: '100%', background: 'var(--popover)', color: 'var(--popover-foreground)', boxShadow: '-8px 0 32px rgba(0,0,0,0.18)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: 20, borderBottom: '1px solid var(--border)' }}>
          <Icon name="triangle-alert" size={18} style={{ color: 'var(--primary)', marginTop: 2 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--muted-foreground)' }}>Decisão de gate</div>
            <h3 style={{ margin: '2px 0 0', fontFamily: 'var(--font-heading)', fontSize: 16, fontWeight: 600, color: 'var(--foreground)' }}>{task.title}</h3>
          </div>
          <Button variant="ghost" size="icon-sm" icon="x" onClick={onClose} aria-label="Fechar" />
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)', marginBottom: 6 }}>O que está sendo pedido</div>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5, color: 'var(--foreground)' }}>{c.pede}</p>
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)', marginBottom: 8 }}>O que as personas produziram</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(c.produzido || []).map((p, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, fontSize: 13, lineHeight: 1.45, color: 'var(--foreground)' }}>
                  <Icon name="circle-check" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0, marginTop: 1 }} /> {p}
                </div>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {c.pr && <a href={c.prUrl} onClick={e => e.preventDefault()} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--primary)', textDecoration: 'none' }}><Icon name="git-branch" size={14} /> {c.pr}</a>}
            {c.budget && <Badge variant="outline">budget {c.budget}</Badge>}
          </div>
          {rejecting && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--foreground)' }}>Motivo da rejeição <span style={{ color: 'var(--destructive)' }}>*</span></span>
              <Textarea value={reason} onChange={e => setReason(e.target.value)} rows={3} placeholder="Explique por que está rejeitando…" />
            </div>
          )}
        </div>

        <div style={{ padding: 20, borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            {!rejecting
              ? <><Button style={{ flex: 1 }} onClick={onClose}>Aprovar</Button>
                  <Button variant="destructive" style={{ flex: 1 }} onClick={() => setRejecting(true)}>Rejeitar</Button></>
              : <><Button variant="ghost" onClick={() => setRejecting(false)}>Voltar</Button>
                  <Button variant="destructive" style={{ flex: 1 }} disabled={!reason.trim()} onClick={onClose}>Confirmar rejeição</Button></>}
          </div>
          <p style={{ margin: 0, fontSize: 11, color: 'var(--muted-foreground)', textAlign: 'center' }}>Sua decisão fica registrada no grafo.</p>
        </div>
      </div>
    </div>
  );
}

function FlowBoard({ flow, onBack }) {
  const { Badge, Icon, Button } = K;
  const [gate, setGate] = useState(null);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%', minHeight: 0, position: 'relative' }}>
      {gate && <GateSheet task={gate} onClose={() => setGate(null)} />}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button onClick={onBack} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 13, color: 'var(--muted-foreground)', fontFamily: 'var(--font-sans)' }}>
          <Icon name="chevron-right" size={14} style={{ transform: 'rotate(180deg)' }} /> Flows
        </button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 18, fontWeight: 600, letterSpacing: '-0.025em', color: 'var(--foreground)' }}>{flow.name}</h2>
            <Badge variant="secondary">{flow.template}</Badge>
            {flow.gate && <Badge icon="triangle-alert">gate aberto</Badge>}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, fontSize: 12, color: 'var(--muted-foreground)' }}>
            <span style={{ display: 'flex', gap: 3 }}>{flow.cast.map((e, i) => <window.PersonaGlyph key={i} glyph={e} size={20} />)}</span>
            <span>budget {flow.budget.used}/{flow.budget.limit} · criado {flow.created}</span>
          </div>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: `repeat(${flow.states.length}, minmax(200px, 1fr))`, gap: 12, overflowX: 'auto', position: 'relative' }}>
        {flow.tasks.length === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1 }}>
            <window.EmptyState icon="workflow" title="Nenhuma task neste flow ainda"
              description={flow.status === 'pausado' ? 'Este flow está pausado. Retome-o para o Operador começar a enfileirar tasks.' : 'Assim que o flow rodar, as tasks aparecem distribuídas pelas colunas do board.'}
              action={<Button size="sm" icon="play">{flow.status === 'pausado' ? 'Retomar flow' : 'Rodar agora'}</Button>} />
          </div>
        )}
        {flow.states.map(state => {
          const items = flow.tasks.filter(t => t.state === state);
          return (
            <div key={state} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, background: 'color-mix(in oklab, var(--muted) 50%, transparent)', borderRadius: 'var(--radius-2xl)', padding: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '2px 4px 10px' }}>
                <span style={{ fontFamily: 'var(--font-heading)', fontSize: 13, fontWeight: 500, color: state === 'failed' ? 'var(--destructive)' : 'var(--foreground)' }}>{STATE_LABEL[state] || state}</span>
                <span style={{ fontSize: 12, color: 'var(--muted-foreground)', background: 'var(--background)', borderRadius: 9999, padding: '1px 8px' }}>{items.length}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
                {items.map(t => <TaskCard key={t.id} t={t} onGate={setGate} />)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FlowsScreen() {
  const { PageHeader, Button, Card, CardContent, Badge, Icon } = K;
  const d = window.KUBO_DATA;
  const sv = window.KUBO_STATUS;
  const [flow, setFlow] = useState(null);

  if (flow) return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24, height: '100%', minHeight: 0 }}>
      <FlowBoard flow={flow} onBack={() => setFlow(null)} />
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Flows" description="Automações multi-persona instanciadas de templates. Abra um flow para ver seu board."
        actions={<Button icon="plus">Novo flow</Button>} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {d.flows.map(f => (
          <button key={f.id} onClick={() => setFlow(f)} style={{ textAlign: 'left', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 16, padding: 16, border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', background: 'var(--card)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 40, height: 40, flexShrink: 0, borderRadius: 'var(--radius-lg)', background: 'var(--muted)' }}>
              <Icon name="workflow" size={18} style={{ color: 'var(--muted-foreground)' }} />
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>{f.name}</span>
                <Badge variant="secondary">{f.template}</Badge>
                {f.gate && <Badge icon="triangle-alert">gate</Badge>}
                <Badge variant={sv(f.status)}>{f.status}</Badge>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, fontSize: 12, color: 'var(--muted-foreground)', flexWrap: 'wrap' }}>
                <span style={{ display: 'flex', gap: 3 }}>{f.cast.map((e, i) => <window.PersonaGlyph key={i} glyph={e} size={20} />)}</span>
                <span>{f.tasksOpen} tasks abertas · budget {f.budget.used}/{f.budget.limit} · criado {f.created}</span>
              </div>
            </div>
            <Icon name="chevron-right" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0 }} />
          </button>
        ))}
      </div>
    </div>
  );
}
window.FlowsScreen = FlowsScreen;
