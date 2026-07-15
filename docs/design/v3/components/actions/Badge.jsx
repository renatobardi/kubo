import { Icon } from '../icons/Icon.jsx';

const VARIANTS = {
  default:     { background: 'var(--primary)', color: 'var(--primary-foreground)', border: '1px solid transparent' },
  secondary:   { background: 'var(--secondary)', color: 'var(--secondary-foreground)', border: '1px solid transparent' },
  destructive: { background: 'color-mix(in oklab, var(--destructive) 10%, transparent)', color: 'var(--destructive)', border: '1px solid transparent' },
  outline:     { background: 'color-mix(in oklab, var(--input) 30%, transparent)', color: 'var(--foreground)', border: '1px solid var(--border)' },
  ghost:       { background: 'transparent', color: 'var(--muted-foreground)', border: '1px solid transparent' },
};

export function Badge({ variant = 'default', icon, children, style, ...rest }) {
  const v = VARIANTS[variant] || VARIANTS.default;
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 4,
        width: 'fit-content', height: 20, padding: '2px 8px', paddingLeft: icon ? 6 : 8,
        borderRadius: 'var(--radius-4xl)', fontSize: 12, fontWeight: 500,
        fontFamily: 'var(--font-sans)', whiteSpace: 'nowrap', overflow: 'hidden',
        ...v, ...style,
      }}
      {...rest}
    >
      {icon && <Icon name={icon} size={12} />}
      {children}
    </span>
  );
}
