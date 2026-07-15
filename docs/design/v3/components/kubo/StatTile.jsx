import { Icon } from '../icons/Icon.jsx';

export function StatTile({ label, value, icon, onClick, style }) {
  return (
    <a href="#" onClick={(e) => { e.preventDefault(); onClick && onClick(); }}
      style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: 16, textDecoration: 'none',
        background: 'var(--card)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius-xl)', boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
        transition: 'all 150ms', ...style,
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'color-mix(in oklab, var(--primary) 30%, transparent)'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.08)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = '0 1px 2px rgba(0,0,0,0.04)'; }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 36, height: 36, flexShrink: 0, borderRadius: 'var(--radius-md)', background: 'var(--muted)', color: 'var(--muted-foreground)' }}>
        <Icon name={icon} size={16} />
      </div>
      <div style={{ minWidth: 0 }}>
        <p style={{ margin: 0, fontSize: 24, lineHeight: 1, fontWeight: 600, letterSpacing: 'var(--tracking-tight)', color: 'var(--foreground)' }}>{value}</p>
        <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--muted-foreground)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</p>
      </div>
    </a>
  );
}
