export function Input({ style, disabled, ...rest }) {
  return (
    <input
      disabled={disabled}
      style={{
        height: 36, width: '100%', minWidth: 0, padding: '4px 12px',
        background: 'color-mix(in oklab, var(--input) 30%, transparent)',
        border: '1px solid var(--input)', borderRadius: 'var(--radius-4xl)',
        fontSize: 14, fontFamily: 'var(--font-sans)', color: 'var(--foreground)',
        outline: 'none', transition: 'box-shadow 150ms, border-color 150ms',
        opacity: disabled ? 0.5 : 1,
        ...style,
      }}
      onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--ring)'; e.currentTarget.style.boxShadow = '0 0 0 3px color-mix(in oklab, var(--ring) 50%, transparent)'; }}
      onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--input)'; e.currentTarget.style.boxShadow = 'none'; }}
      {...rest}
    />
  );
}
