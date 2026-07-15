import { Icon } from '../icons/Icon.jsx';

export function Breadcrumb({ segments = [] }) {
  return (
    <nav aria-label="breadcrumb">
      <ol style={{ display: 'flex', alignItems: 'center', gap: 8, margin: 0, padding: 0, listStyle: 'none', fontSize: 14 }}>
        {segments.map((seg, i) => {
          const last = i === segments.length - 1;
          return (
            <li key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {last ? (
                <span style={{ fontWeight: 500, color: 'var(--foreground)' }}>{seg.label}</span>
              ) : (
                <a href={seg.href || '#'} style={{ color: 'var(--muted-foreground)', textDecoration: 'none' }}
                   onMouseEnter={(e) => e.currentTarget.style.color = 'var(--foreground)'}
                   onMouseLeave={(e) => e.currentTarget.style.color = 'var(--muted-foreground)'}>
                  {seg.label}
                </a>
              )}
              {!last && <Icon name="chevron-right" size={14} style={{ color: 'var(--muted-foreground)' }} />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
