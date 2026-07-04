import { useState } from 'react';
import { Button } from '../actions/Button.jsx';

export function Dialog({ open: controlled, defaultOpen = false, onOpenChange, trigger, title, description, children, footer, showClose = true }) {
  const [internal, setInternal] = useState(defaultOpen);
  const open = controlled ?? internal;
  const set = (v) => { if (controlled === undefined) setInternal(v); onOpenChange && onOpenChange(v); };
  return (
    <>
      {trigger && <span onClick={() => set(true)}>{trigger}</span>}
      {open && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={() => set(false)} style={{ position: 'absolute', inset: 0, background: 'color-mix(in oklab, black 50%, transparent)' }} />
          <div style={{
            position: 'relative', width: '100%', maxWidth: 448, display: 'grid', gap: 24, padding: 24,
            background: 'var(--popover)', color: 'var(--popover-foreground)',
            borderRadius: 'var(--radius-4xl)', fontSize: 14,
            boxShadow: '0 0 0 1px color-mix(in oklab, var(--foreground) 5%, transparent), 0 24px 48px rgba(0,0,0,0.24)',
          }}>
            {(title || description) && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {title && <h2 style={{ margin: 0, fontFamily: 'var(--font-heading)', fontSize: 18, fontWeight: 600, letterSpacing: 'var(--tracking-tight)' }}>{title}</h2>}
                {description && <p style={{ margin: 0, fontSize: 14, color: 'var(--muted-foreground)' }}>{description}</p>}
              </div>
            )}
            {children}
            {footer && <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>{footer}</div>}
            {showClose && (
              <div style={{ position: 'absolute', top: 16, right: 16 }}>
                <Button variant="ghost" size="icon-sm" icon="x" onClick={() => set(false)} aria-label="Close" />
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
