import { Icon } from '../icons/Icon.jsx';

const TOP = [{ title: 'Home', icon: 'house' }];
const GROUPS = [
  { label: 'Conhecimento', items: [{ title: 'Conhecimento', icon: 'book-open' }, { title: 'Fontes', icon: 'rss' }] },
  { label: 'Trabalho', items: [{ title: 'Flows', icon: 'workflow' }, { title: 'Execuções', icon: 'activity' }] },
  { label: 'Distribuição', items: [{ title: 'Destinos', icon: 'send' }, { title: 'Envios', icon: 'mail' }] },
  { label: 'Catálogos', items: [{ title: 'Integrações', icon: 'blocks' }, { title: 'Personas', icon: 'user' }, { title: 'Templates', icon: 'git-branch' }] },
];

export function Sidebar({ top = TOP, groups = GROUPS, active = 'Home', onNavigate, user = { name: 'Renato Bardi', email: 'renato@kubo.studio' }, brand = 'Kubo', tagline = 'The art of getting things done' }) {
  const initial = (user.name || user.email || '?').charAt(0).toUpperCase();
  const NavButton = (item) => {
    const isActive = item.title === active;
    return (
      <button key={item.title} onClick={() => onNavigate && onNavigate(item.title)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%', height: 32, padding: '0 8px',
          border: 'none', borderRadius: 'var(--radius-lg)', cursor: 'pointer',
          fontSize: 14, fontFamily: 'var(--font-sans)', textAlign: 'left',
          background: isActive ? 'var(--sidebar-accent)' : 'transparent',
          color: isActive ? 'var(--sidebar-accent-foreground)' : 'var(--sidebar-foreground)',
          fontWeight: isActive ? 500 : 400,
        }}
        onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--sidebar-accent)'; }}
        onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
      >
        <Icon name={item.icon} size={16} style={{ color: isActive ? 'var(--sidebar-primary)' : 'var(--muted-foreground)' }} />
        <span>{item.title}</span>
      </button>
    );
  };
  return (
    <aside style={{
      width: 256, flexShrink: 0, height: '100%', display: 'flex', flexDirection: 'column',
      background: 'var(--sidebar)', color: 'var(--sidebar-foreground)',
      borderRight: '1px solid var(--sidebar-border)',
    }}>
      {/* Header — brand */}
      <div style={{ padding: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 56, padding: '0 8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: 'var(--radius-lg)', background: 'var(--sidebar-primary)', color: 'var(--sidebar-primary-foreground)', fontFamily: "'Noto Sans JP', var(--font-sans)", fontWeight: 600, fontSize: 19, flexShrink: 0 }}>智</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 1, lineHeight: 1 }}>
            <span style={{ fontFamily: 'var(--font-heading)', fontSize: 16, fontWeight: 600, letterSpacing: 'var(--tracking-tight)' }}>{brand}</span>
            <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{tagline}</span>
          </div>
        </div>
      </div>

      {/* Nav */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {top.map(NavButton)}
        </nav>
        {groups.map(group => (
          <div key={group.label} style={{ marginTop: 12 }}>
            <div style={{ padding: '4px 8px', fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--muted-foreground)' }}>{group.label}</div>
            <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {group.items.map(NavButton)}
            </nav>
          </div>
        ))}
      </div>

      {/* Footer — single owner → settings */}
      <div style={{ padding: 8, borderTop: '1px solid var(--sidebar-border)' }}>
        <div onClick={() => onNavigate && onNavigate('Configurações')} title="Configurações"
          style={{ display: 'flex', alignItems: 'center', gap: 8, height: 48, padding: '0 8px', borderRadius: 'var(--radius-lg)', cursor: 'pointer',
            background: active === 'Configurações' ? 'var(--sidebar-accent)' : 'transparent' }}
          onMouseEnter={(e) => { if (active !== 'Configurações') e.currentTarget.style.background = 'var(--sidebar-accent)'; }}
          onMouseLeave={(e) => { if (active !== 'Configurações') e.currentTarget.style.background = 'transparent'; }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: 'var(--radius-lg)', background: 'var(--primary)', color: 'var(--primary-foreground)', fontSize: 12, fontWeight: 600, flexShrink: 0 }}>{initial}</div>
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1, lineHeight: 1.3 }}>
            <span style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{user.name}</span>
            <span style={{ fontSize: 12, color: 'var(--muted-foreground)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{user.email}</span>
          </div>
          <Icon name="settings" size={16} style={{ color: 'var(--muted-foreground)', flexShrink: 0 }} />
        </div>
      </div>
    </aside>
  );
}
