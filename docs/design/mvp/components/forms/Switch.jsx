import { useState } from 'react';

export function Switch({ checked: controlled, defaultChecked = false, onCheckedChange, size = 'default', disabled, style }) {
  const [internal, setInternal] = useState(defaultChecked);
  const checked = controlled ?? internal;
  const dims = size === 'sm' ? { w: 24, h: 14, thumb: 12 } : { w: 32, h: 18.4, thumb: 16 };
  const toggle = () => {
    if (disabled) return;
    const next = !checked;
    if (controlled === undefined) setInternal(next);
    onCheckedChange && onCheckedChange(next);
  };
  return (
    <button
      type="button" role="switch" aria-checked={checked} onClick={toggle} disabled={disabled}
      style={{
        position: 'relative', flexShrink: 0, display: 'inline-flex', alignItems: 'center',
        width: dims.w, height: dims.h, padding: 0, border: '1px solid transparent',
        borderRadius: 9999, cursor: disabled ? 'not-allowed' : 'pointer',
        background: checked ? 'var(--primary)' : 'var(--input)',
        transition: 'background 150ms', outline: 'none', opacity: disabled ? 0.5 : 1,
        ...style,
      }}
    >
      <span style={{
        display: 'block', width: dims.thumb, height: dims.thumb, borderRadius: 9999,
        background: 'var(--background)', transition: 'transform 150ms',
        transform: checked ? `translateX(${dims.w - dims.thumb - 2}px)` : 'translateX(1px)',
        boxShadow: '0 1px 2px rgba(0,0,0,0.15)',
      }} />
    </button>
  );
}
