import { Icon } from '../icons/Icon.jsx';

const VARIANTS = {
  default:     { background: 'var(--primary)', color: 'var(--primary-foreground)', border: '1px solid transparent', '--hoverBg': 'color-mix(in oklab, var(--primary) 80%, transparent)' },
  outline:     { background: 'color-mix(in oklab, var(--input) 30%, transparent)', color: 'var(--foreground)', border: '1px solid var(--border)', '--hoverBg': 'color-mix(in oklab, var(--input) 50%, transparent)' },
  secondary:   { background: 'var(--secondary)', color: 'var(--secondary-foreground)', border: '1px solid transparent', '--hoverBg': 'color-mix(in oklab, var(--secondary) 80%, transparent)' },
  ghost:       { background: 'transparent', color: 'var(--foreground)', border: '1px solid transparent', '--hoverBg': 'var(--muted)' },
  destructive: { background: 'color-mix(in oklab, var(--destructive) 10%, transparent)', color: 'var(--destructive)', border: '1px solid transparent', '--hoverBg': 'color-mix(in oklab, var(--destructive) 20%, transparent)' },
  link:        { background: 'transparent', color: 'var(--primary)', border: '1px solid transparent', textDecoration: 'underline', textUnderlineOffset: '4px' },
};

const SIZES = {
  default: { height: 36, padding: '0 12px', fontSize: 14, gap: 6 },
  sm:      { height: 32, padding: '0 12px', fontSize: 14, gap: 4 },
  xs:      { height: 24, padding: '0 10px', fontSize: 12, gap: 4 },
  lg:      { height: 40, padding: '0 16px', fontSize: 14, gap: 6 },
  icon:    { height: 36, width: 36, padding: 0, gap: 0 },
  'icon-sm': { height: 32, width: 32, padding: 0, gap: 0 },
  'icon-xs': { height: 24, width: 24, padding: 0, gap: 0 },
  'icon-lg': { height: 40, width: 40, padding: 0, gap: 0 },
};

export function Button({ variant = 'default', size = 'default', icon, iconEnd, disabled, children, style, ...rest }) {
  const v = VARIANTS[variant] || VARIANTS.default;
  const s = SIZES[size] || SIZES.default;
  const { '--hoverBg': hoverBg, ...vStyle } = v;
  const isIcon = size.startsWith('icon');
  return (
    <button
      disabled={disabled}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        whiteSpace: 'nowrap', fontWeight: 500, fontFamily: 'var(--font-sans)',
        borderRadius: 'var(--radius-4xl)', cursor: 'pointer', userSelect: 'none',
        transition: 'all 150ms', outline: 'none', flexShrink: 0,
        height: s.height, width: s.width, padding: s.padding, gap: s.gap, fontSize: s.fontSize,
        opacity: disabled ? 0.5 : 1, pointerEvents: disabled ? 'none' : undefined,
        ...vStyle, ...style,
      }}
      onMouseDown={(e) => { if (variant !== 'link') e.currentTarget.style.transform = 'translateY(1px)'; }}
      onMouseUp={(e) => { e.currentTarget.style.transform = ''; }}
      onMouseEnter={(e) => { if (hoverBg) e.currentTarget.style.background = hoverBg; if (variant === 'link') e.currentTarget.style.textDecoration = 'underline'; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = vStyle.background; e.currentTarget.style.transform = ''; }}
      {...rest}
    >
      {icon && <Icon name={icon} size={size === 'xs' || size === 'icon-xs' ? 12 : 16} />}
      {!isIcon && children}
      {isIcon && !icon && children}
      {iconEnd && <Icon name={iconEnd} size={16} />}
    </button>
  );
}
