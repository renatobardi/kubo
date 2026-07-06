// Kubo logo. The mark is a five-petal sakura (cherry blossom) drawn in line style —
// a nod to the "atelier" idea. Mono, near-black. No colored variant.
//   variant="mark"  → line sakura inside a near-black tile (app icon / favicon)
//   variant="full"  → loose line sakura (no tile) beside the "Kubo" wordmark (+ tagline)
//   variant="glyph" → the bare blossom on its own, near-black on the page

// Reusable blossom: 5 notched petals rotated 72°, radiating stamens + center dot.
// Theme-aware by default — petal fill and ink follow --sakura-petal / --sakura-ink
// (light: pink petals + near-black ink · dark: no fill + pink ink). Override with props.
export function Sakura({ size = 32, stroke, fill, sw = 6, style }) {
  const ink = stroke || 'var(--sakura-ink)';
  const petalFill = fill || 'var(--sakura-petal)';
  const petal = 'M50,50 C38,43 33,27 39,15 C42,8 47,10 50,17 C53,10 58,8 61,15 C67,27 62,43 50,50 Z';
  const arms = [0, 1, 2, 3, 4];
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" style={{ display: 'block', ...style }}>
      {arms.map((i) => (
        <path key={'p' + i} d={petal} transform={`rotate(${i * 72} 50 50)`} fill={petalFill} stroke={ink} strokeWidth={sw} strokeLinejoin="round" />
      ))}
      {arms.map((i) => (
        <line key={'l' + i} x1="50" y1="50" x2="50" y2="34" transform={`rotate(${i * 72 + 36} 50 50)`} stroke={ink} strokeWidth={sw * 0.55} strokeLinecap="round" />
      ))}
      {arms.map((i) => (
        <circle key={'c' + i} cx="50" cy="33" r={sw * 0.5} transform={`rotate(${i * 72 + 36} 50 50)`} fill={ink} />
      ))}
      <circle cx="50" cy="50" r={sw * 0.9} fill={ink} />
    </svg>
  );
}

export function Logo({ variant = 'full', size = 32, color, onDark = false, tagline = 'The art of getting things done', style }) {
  const fg = color || 'var(--foreground)';
  const markBg = 'var(--sidebar-primary)';
  const markFg = 'var(--sidebar-primary-foreground)';

  // Line sakura in a near-black tile — the compact app-icon mark (option 1b).
  // The tile is a fixed dark surface, so it always uses the pink outline treatment.
  const TileMark = (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, flexShrink: 0,
      borderRadius: `calc(${size}px * 0.26)`, background: markBg,
    }}>
      <Sakura size={size * 0.68} stroke="#f4c9d4" fill="none" sw={6} />
    </div>
  );

  if (variant === 'mark') {
    return <span style={{ display: 'inline-flex', ...style }}>{TileMark}</span>;
  }

  // Bare blossom on its own — theme-aware (pink petals on light, pink outline on dark).
  if (variant === 'glyph') {
    return <span style={{ display: 'inline-flex', ...style }}><Sakura size={size} sw={6} /></span>;
  }

  // Full lockup: loose theme-aware sakura (no tile) + wordmark (+ optional tagline).
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: size * 0.4, ...style }}>
      <Sakura size={size * 1.05} sw={6} />
      <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.05 }}>
        <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: size * 0.85, letterSpacing: '-0.025em', color: fg }}>Kubo</span>
        {tagline && <span style={{ fontFamily: 'var(--font-sans)', fontSize: size * 0.34, color: 'var(--muted-foreground)', marginTop: size * 0.06 }}>{tagline}</span>}
      </span>
    </span>
  );
}
