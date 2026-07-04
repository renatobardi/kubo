export function Label({ children, style, ...rest }) {
  return (
    <label
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        fontSize: 14, fontWeight: 500, lineHeight: 1, userSelect: 'none',
        fontFamily: 'var(--font-sans)', color: 'var(--foreground)',
        ...style,
      }}
      {...rest}
    >
      {children}
    </label>
  );
}
