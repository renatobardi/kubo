export function Card({ size = 'default', children, style, ...rest }) {
  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column',
        gap: size === 'sm' ? 16 : 24, paddingTop: size === 'sm' ? 16 : 24, paddingBottom: size === 'sm' ? 16 : 24,
        background: 'var(--card)', color: 'var(--card-foreground)',
        borderRadius: 'var(--radius-2xl)', fontSize: 14, overflow: 'hidden',
        boxShadow: '0 0 0 1px color-mix(in oklab, var(--foreground) 10%, transparent)',
        ...style,
      }}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, style, ...rest }) {
  return (
    <div style={{ display: 'grid', gridAutoRows: 'min-content', gap: 6, padding: '0 24px', gridTemplateColumns: '1fr auto', ...style }} {...rest}>
      {children}
    </div>
  );
}

export function CardTitle({ children, style, ...rest }) {
  return (
    <h3 style={{ gridColumn: 1, margin: 0, fontFamily: 'var(--font-heading)', fontSize: 16, fontWeight: 500, letterSpacing: 'var(--tracking-tight)', lineHeight: 1.2, ...style }} {...rest}>
      {children}
    </h3>
  );
}

export function CardDescription({ children, style, ...rest }) {
  return (
    <p style={{ gridColumn: 1, margin: 0, fontSize: 12, color: 'var(--muted-foreground)', ...style }} {...rest}>
      {children}
    </p>
  );
}

export function CardAction({ children, style, ...rest }) {
  return (
    <div style={{ gridColumn: 2, gridRow: '1 / span 2', alignSelf: 'start', justifySelf: 'end', ...style }} {...rest}>
      {children}
    </div>
  );
}

export function CardContent({ children, style, ...rest }) {
  return (
    <div style={{ padding: '0 24px', ...style }} {...rest}>
      {children}
    </div>
  );
}

export function CardFooter({ children, style, ...rest }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', padding: '0 24px', ...style }} {...rest}>
      {children}
    </div>
  );
}
