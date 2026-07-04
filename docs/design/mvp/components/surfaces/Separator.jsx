export function Separator({ orientation = 'horizontal', style, ...rest }) {
  return (
    <div
      role="separator"
      style={{
        flexShrink: 0, background: 'var(--border)',
        width: orientation === 'vertical' ? 1 : '100%',
        height: orientation === 'vertical' ? '100%' : 1,
        ...style,
      }}
      {...rest}
    />
  );
}
