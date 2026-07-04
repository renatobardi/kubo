export function Skeleton({ style, ...rest }) {
  return (
    <div
      style={{
        background: 'var(--muted)', borderRadius: 'var(--radius-xl)',
        animation: 'kubo-pulse 1.5s ease-in-out infinite',
        ...style,
      }}
      {...rest}
    >
      <style>{`@keyframes kubo-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }`}</style>
    </div>
  );
}

export function Tooltip({ label, side = 'top', children }) {
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }} className="kubo-tt">
      {children}
      <span style={{
        position: 'absolute', zIndex: 50, pointerEvents: 'none', opacity: 0,
        transition: 'opacity 120ms', whiteSpace: 'nowrap',
        ...(side === 'top' ? { bottom: '100%', left: '50%', transform: 'translateX(-50%)', marginBottom: 6 } :
           side === 'right' ? { left: '100%', top: '50%', transform: 'translateY(-50%)', marginLeft: 6 } :
           side === 'bottom' ? { top: '100%', left: '50%', transform: 'translateX(-50%)', marginTop: 6 } :
           { right: '100%', top: '50%', transform: 'translateY(-50%)', marginRight: 6 }),
        background: 'var(--foreground)', color: 'var(--background)',
        fontSize: 12, padding: '6px 12px', borderRadius: 'var(--radius-2xl)',
      }} className="kubo-tt-bubble">
        {label}
      </span>
      <style>{`.kubo-tt:hover .kubo-tt-bubble { opacity: 1 !important; }`}</style>
    </span>
  );
}
