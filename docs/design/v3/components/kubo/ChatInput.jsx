import { useState, useRef } from 'react';
import { Button } from '../actions/Button.jsx';

export function ChatInput({ onSend, placeholder = 'Message…', disabled = false, hint = true }) {
  const [value, setValue] = useState('');
  const ref = useRef(null);
  const canSend = value.trim().length > 0 && !disabled;
  const resize = () => {
    const el = ref.current; if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  };
  const send = () => {
    if (!canSend) return;
    onSend && onSend(value.trim());
    setValue('');
    if (ref.current) ref.current.style.height = 'auto';
  };
  return (
    <div style={{ background: 'var(--background)', padding: '12px 16px 16px' }}>
      <div style={{
        display: 'flex', alignItems: 'flex-end', gap: 4, padding: 12,
        background: 'var(--card)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius-2xl)', boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
        transition: 'border-color 150ms, box-shadow 150ms',
      }}
      onFocusCapture={(e) => { e.currentTarget.style.borderColor = 'color-mix(in oklab, var(--ring) 50%, transparent)'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.08)'; }}
      onBlurCapture={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = '0 1px 2px rgba(0,0,0,0.04)'; }}
      >
        <Button variant="ghost" size="icon" icon="paperclip" disabled={disabled} style={{ width: 32, height: 32, color: 'var(--muted-foreground)' }} aria-label="Attach file" />
        <textarea
          ref={ref} value={value} rows={1} placeholder={placeholder} disabled={disabled}
          onChange={(e) => { setValue(e.target.value); resize(); }}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          style={{
            flex: 1, minWidth: 0, maxHeight: 160, border: 'none', background: 'transparent',
            resize: 'none', outline: 'none', padding: '8px', fontSize: 14, lineHeight: 1.5,
            fontFamily: 'var(--font-sans)', color: 'var(--foreground)',
          }}
        />
        <Button variant={canSend ? 'default' : 'ghost'} size="icon" icon="arrow-up" onClick={send} disabled={!canSend} style={{ width: 32, height: 32 }} aria-label="Send message" />
      </div>
      {hint && <p style={{ margin: '8px 0 0', textAlign: 'center', fontSize: 10, color: 'color-mix(in oklab, var(--muted-foreground) 60%, transparent)' }}>Enter to send · Shift+Enter for new line</p>}
    </div>
  );
}
