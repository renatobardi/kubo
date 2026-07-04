// Kubo logo. The mark is the kanji 智 (chi = wisdom / intellect) in a tile,
// beside the "Kubo" wordmark. There is no separate drawn logotype.
// Kanji set in Noto Sans JP to match the Inter wordmark.

export function Logo({ variant = 'full', size = 32, color, onDark = false, tagline = 'The art of getting things done', markGlyph = '智', style }) {
  const fg = color || (onDark ? 'var(--foreground)' : 'var(--foreground)');
  const markBg = 'var(--sidebar-primary)';
  const markFg = 'var(--sidebar-primary-foreground)';

  // The kanji mark in a near-black tile. Default 智 (chi) = "wisdom / intellect".
  const Mark = (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, flexShrink: 0,
      borderRadius: `calc(${size}px * 0.26)`, background: markBg, color: markFg,
      fontFamily: "'Noto Sans JP', var(--font-sans)", fontWeight: 600,
      fontSize: size * 0.6, lineHeight: 1,
    }}>{markGlyph}</div>
  );

  if (variant === 'mark') {
    return <span style={{ display: 'inline-flex', ...style }}>{Mark}</span>;
  }

  // Kanji-only lockup — the 智 mark as a large pure typographic glyph
  if (variant === 'kanji') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'baseline',
        fontFamily: "'Noto Sans JP', var(--font-sans)", fontWeight: 600,
        fontSize: size, lineHeight: 1, color: fg, ...style }}>
        {markGlyph}
      </span>
    );
  }

  // Full lockup: mark + wordmark (+ optional tagline)
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: size * 0.34, ...style }}>
      {Mark}
      <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.05 }}>
        <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: size * 0.85, letterSpacing: '-0.025em', color: fg }}>Kubo</span>
        {tagline && <span style={{ fontFamily: 'var(--font-sans)', fontSize: size * 0.34, color: 'var(--muted-foreground)', marginTop: size * 0.06 }}>{tagline}</span>}
      </span>
    </span>
  );
}
