export function PageHeader({ title, description, actions }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 600, letterSpacing: 'var(--tracking-tight)', color: 'var(--foreground)' }}>{title}</h1>
          {description && <p style={{ margin: '4px 0 0', fontSize: 14, color: 'var(--muted-foreground)' }}>{description}</p>}
        </div>
        {actions && <div style={{ flexShrink: 0 }}>{actions}</div>}
      </div>
      <div style={{ marginTop: 16, height: 1, width: '100%', background: 'var(--border)' }} />
    </div>
  );
}
