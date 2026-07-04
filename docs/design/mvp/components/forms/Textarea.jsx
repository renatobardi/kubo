export function Textarea({ style, disabled, rows = 4, ...rest }) {
  return (
    <textarea
      disabled={disabled}
      rows={rows}
      style={{
        width: '100%', minHeight: 64, padding: '12px', resize: 'none',
        background: 'color-mix(in oklab, var(--input) 30%, transparent)',
        border: '1px solid var(--input)', borderRadius: 'var(--radius-xl)',
        fontSize: 14, fontFamily: 'var(--font-sans)', color: 'var(--foreground)',
        lineHeight: 1.5, outline: 'none', transition: 'box-shadow 150ms, border-color 150ms',
        opacity: disabled ? 0.5 : 1,
        ...style,
      }}
      onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--ring)'; e.currentTarget.style.boxShadow = '0 0 0 3px color-mix(in oklab, var(--ring) 50%, transparent)'; }}
      onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--input)'; e.currentTarget.style.boxShadow = 'none'; }}
      {...rest}
    />
  );
}
