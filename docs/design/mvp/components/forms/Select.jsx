import { useState, useRef, useEffect } from 'react';
import { Icon } from '../icons/Icon.jsx';

export function Select({ options = [], value: controlled, defaultValue, placeholder = 'Select…', onValueChange, size = 'default', disabled, style }) {
  const [internal, setInternal] = useState(defaultValue);
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const value = controlled ?? internal;
  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);
  const opts = options.map(o => typeof o === 'string' ? { value: o, label: o } : o);
  const selected = opts.find(o => o.value === value);
  const pick = (v) => {
    if (controlled === undefined) setInternal(v);
    onValueChange && onValueChange(v);
    setOpen(false);
  };
  return (
    <div ref={ref} style={{ position: 'relative', width: 'fit-content', ...style }}>
      <button
        type="button" disabled={disabled} onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
          height: size === 'sm' ? 32 : 36, padding: '0 12px', minWidth: 160,
          background: 'color-mix(in oklab, var(--input) 30%, transparent)',
          border: '1px solid var(--input)', borderRadius: 'var(--radius-4xl)',
          fontSize: 14, fontFamily: 'var(--font-sans)', cursor: 'pointer', outline: 'none',
          color: selected ? 'var(--foreground)' : 'var(--muted-foreground)',
          opacity: disabled ? 0.5 : 1, whiteSpace: 'nowrap',
        }}
      >
        <span>{selected ? selected.label : placeholder}</span>
        <Icon name="chevron-down" size={16} style={{ color: 'var(--muted-foreground)' }} />
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 50, minWidth: '100%',
          background: 'var(--popover)', color: 'var(--popover-foreground)',
          border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)',
          boxShadow: '0 0 0 1px color-mix(in oklab, var(--foreground) 5%, transparent), 0 8px 24px rgba(0,0,0,0.12)',
          padding: 4,
        }}>
          {opts.map(o => (
            <div key={o.value} onClick={() => pick(o.value)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '6px 8px', borderRadius: 'var(--radius-md)', fontSize: 14,
                cursor: 'pointer', whiteSpace: 'nowrap',
                background: o.value === value ? 'var(--accent)' : 'transparent',
              }}
              onMouseEnter={(e) => e.currentTarget.style.background = 'var(--accent)'}
              onMouseLeave={(e) => e.currentTarget.style.background = o.value === value ? 'var(--accent)' : 'transparent'}
            >
              {o.label}
              {o.value === value && <Icon name="check" size={16} style={{ color: 'var(--primary)' }} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
