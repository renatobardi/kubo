// App shell: sidebar + 72px header with breadcrumb. Uses design-system components.
const K = window.KoboDesignSystem_6efae6;

function Shell({ page, breadcrumb, onNavigate, dark, onToggleDark, authed = true, onLoginClick, onOpenCmd, children }) {
  const { Sidebar, Breadcrumb, Icon, Tooltip } = K;
  return (
    <div style={{ display: 'flex', height: '100%', background: 'var(--background)' }}>
      <div style={{ height: '100%', position: 'relative' }}>
        <div style={{ height: '100%', opacity: authed ? 1 : 0.4, filter: authed ? 'none' : 'saturate(0)', pointerEvents: authed ? 'auto' : 'none' }}>
          <Sidebar active={page} onNavigate={onNavigate} user={window.KUBO_DATA.owner} />
        </div>
        {!authed && (
          <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: 8, borderTop: '1px solid var(--sidebar-border)', background: 'var(--sidebar)' }}>
            <button onClick={onLoginClick} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '10px 8px', border: '1px dashed color-mix(in oklab, var(--foreground) 22%, transparent)', borderRadius: 'var(--radius-lg)', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: 9999, background: 'var(--muted)', color: 'var(--muted-foreground)', flexShrink: 0 }}><Icon name="user" size={16} /></div>
              <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.3 }}>
                <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--foreground)' }}>Entrar ou criar conta</span>
                <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>para usar seu ateliê</span>
              </div>
            </button>
          </div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <header style={{ display: 'flex', alignItems: 'center', gap: 8, height: 72, flexShrink: 0, padding: '0 16px', borderBottom: '1px solid color-mix(in oklab, var(--border) 50%, transparent)' }}>
          <Tooltip label="Recolher menu" side="bottom">
            <button style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, border: 'none', background: 'transparent', borderRadius: 'var(--radius-md)', cursor: 'pointer', color: 'var(--muted-foreground)' }}>
              <Icon name="panel-left" size={16} />
            </button>
          </Tooltip>
          <div style={{ width: 1, height: 16, background: 'color-mix(in oklab, var(--border) 60%, transparent)', margin: '0 4px' }} />
          <Breadcrumb segments={breadcrumb} />
          <div style={{ flex: 1 }} />
          {authed && <button onClick={onOpenCmd} aria-label="Buscar (⌘K)"
            style={{ display: 'flex', alignItems: 'center', gap: 8, height: 32, padding: '0 10px', border: '1px solid var(--border)', borderRadius: 'var(--radius-4xl)', cursor: 'pointer', background: 'transparent', color: 'var(--muted-foreground)', fontFamily: 'var(--font-sans)' }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--muted)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
            <Icon name="search" size={14} />
            <span style={{ fontSize: 13 }}>Buscar</span>
            <span style={{ fontSize: 11, border: '1px solid var(--border)', borderRadius: 5, padding: '1px 5px', fontFamily: 'ui-monospace, monospace' }}>⌘K</span>
          </button>}
          {authed && <Tooltip label="Configurações" side="bottom">
            <button onClick={() => onNavigate('Configurações')} aria-label="Configurações"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, border: 'none', borderRadius: 'var(--radius-lg)', cursor: 'pointer',
                background: page === 'Configurações' ? 'var(--muted)' : 'transparent', color: page === 'Configurações' ? 'var(--foreground)' : 'var(--muted-foreground)' }}
              onMouseEnter={(e) => { if (page !== 'Configurações') e.currentTarget.style.background = 'var(--muted)'; }}
              onMouseLeave={(e) => { if (page !== 'Configurações') e.currentTarget.style.background = 'transparent'; }}>
              <Icon name="settings" size={16} />
            </button>
          </Tooltip>}
        </header>
        <main style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
          {children}
        </main>
      </div>
    </div>
  );
}
window.Shell = Shell;

// Monochrome persona identity — maps each preset emoji to a Lucide glyph so
// personas follow the same monochrome iconography as the rest of the app.
window.PERSONA_ICON = {
  '🔍': 'search', '🧭': 'network', '🛠️': 'git-branch', '⚖️': 'circle-check',
  '⚗️': 'filter', '⚙️': 'cpu', '🧑': 'user',
};
function PersonaGlyph({ glyph, size = 20, title, tone = 'muted' }) {
  const { Icon } = window.KoboDesignSystem_6efae6;
  const name = window.PERSONA_ICON[glyph] || 'bot';
  const bg = tone === 'primary' ? 'var(--primary)' : 'var(--muted)';
  const fg = tone === 'primary' ? 'var(--primary-foreground)' : 'var(--muted-foreground)';
  return (
    <span title={title} style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, borderRadius: 9999, background: bg, color: fg, flexShrink: 0 }}>
      <Icon name={name} size={Math.round(size * 0.56)} />
    </span>
  );
}
window.PersonaGlyph = PersonaGlyph;

// Reusable search bar — the Conhecimento pattern (lupa + Input, maxWidth 420).
function SearchBar({ value, onChange, placeholder = 'Buscar…' }) {
  const { Input, Icon } = window.KoboDesignSystem_6efae6;
  return (
    <div style={{ position: 'relative', maxWidth: 420 }}>
      <span style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted-foreground)', pointerEvents: 'none' }}><Icon name="search" size={16} /></span>
      <Input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} style={{ paddingLeft: 34 }} />
    </div>
  );
}
window.SearchBar = SearchBar;

// View toggle — segmented control: Lista / Duas colunas / Quadrados.
// Options not in `allowed` render greyed + disabled with an explanatory tooltip.
function ViewToggle({ value, onChange, allowed = ['list', 'grid2', 'squares'] }) {
  const { Icon } = window.KoboDesignSystem_6efae6;
  const opts = [
    { key: 'list', icon: 'list', label: 'Lista' },
    { key: 'grid2', icon: 'columns-2', label: 'Duas colunas' },
    { key: 'squares', icon: 'grid-2x2', label: 'Quadrados' },
  ];
  return (
    <div style={{ display: 'inline-flex', padding: 3, gap: 2, background: 'var(--muted)', borderRadius: 'var(--radius-4xl)', flexShrink: 0 }}>
      {opts.map(o => {
        const on = value === o.key;
        const dis = !allowed.includes(o.key);
        return (
          <button key={o.key} disabled={dis} onClick={() => !dis && onChange(o.key)} aria-label={o.label}
            title={dis ? o.label + ' — não se aplica a esta tela' : o.label}
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 26, border: 'none',
              cursor: dis ? 'not-allowed' : 'pointer', borderRadius: 'calc(var(--radius-4xl) - 2px)',
              background: on ? 'var(--background)' : 'transparent',
              color: dis ? 'var(--muted-foreground)' : on ? 'var(--foreground)' : 'var(--muted-foreground)',
              opacity: dis ? 0.4 : 1,
              boxShadow: on ? '0 0 0 1px color-mix(in oklab, var(--foreground) 8%, transparent)' : 'none', transition: 'all 120ms' }}>
            <Icon name={o.icon} size={15} />
          </button>
        );
      })}
    </div>
  );
}
window.ViewToggle = ViewToggle;

// Case-insensitive substring match across any number of fields.
window.matchQuery = function (query, ...fields) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return true;
  return fields.some(f => String(f == null ? '' : f).toLowerCase().includes(q));
};

// Reusable empty / first-run state.
function EmptyState({ icon = 'sparkles', title, description, action }) {
  const { Icon, Button } = window.KoboDesignSystem_6efae6;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, padding: '56px 24px', textAlign: 'center' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 48, height: 48, borderRadius: 9999, background: 'var(--muted)', color: 'var(--muted-foreground)' }}>
        <Icon name={icon} size={22} />
      </div>
      <div style={{ maxWidth: 340 }}>
        <p style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 15, fontWeight: 600, color: 'var(--foreground)' }}>{title}</p>
        {description && <p style={{ margin: '6px 0 0', fontSize: 13, lineHeight: 1.5, color: 'var(--muted-foreground)' }}>{description}</p>}
      </div>
      {action && <div style={{ marginTop: 4 }}>{action}</div>}
    </div>
  );
}
window.EmptyState = EmptyState;

// Monochrome sparkline (SVG). values: number[]. Uses currentColor via stroke.
function Sparkline({ values = [], width = 96, height = 28, stroke = 'var(--foreground)', fill = true }) {
  if (!values.length) return null;
  const max = Math.max(...values, 1), min = Math.min(...values, 0);
  const span = max - min || 1;
  const stepX = width / (values.length - 1 || 1);
  const pts = values.map((v, i) => [i * stepX, height - ((v - min) / span) * (height - 4) - 2]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const area = `${line} L${width} ${height} L0 ${height} Z`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block', overflow: 'visible' }}>
      {fill && <path d={area} fill="color-mix(in oklab, var(--foreground) 8%, transparent)" />}
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2.2" fill={stroke} />
    </svg>
  );
}
window.Sparkline = Sparkline;

