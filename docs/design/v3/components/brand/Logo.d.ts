/**
 * Kubo logo. The mark is a five-petal sakura (cherry blossom) in line style, beside the "Kubo" wordmark.
 * @startingPoint section="Brand" subtitle="Sakura mark + full lockup" viewport="700x120"
 */
export interface LogoProps {
  /** full = loose line sakura + "Kubo" (+tagline) · mark = sakura in near-black tile · glyph = bare blossom */
  variant?: 'full' | 'mark' | 'glyph';
  /** Base pixel size (tile edge for full/mark; blossom size for glyph) */
  size?: number;
  /** Override the foreground color (defaults to --foreground) */
  color?: string;
  /** Render for a dark surface */
  onDark?: boolean;
  /** Tagline under the wordmark in the full lockup; pass '' to hide */
  tagline?: string;
  style?: React.CSSProperties;
}

/** The bare five-petal sakura SVG mark — reusable outside the Logo lockup. Theme-aware by default. */
export interface SakuraProps {
  size?: number;
  /** Line/stamen color. Defaults to --sakura-ink (near-black on light, pink on dark). */
  stroke?: string;
  /** Petal fill. Defaults to --sakura-petal (pink on light, transparent on dark). */
  fill?: string;
  sw?: number;
  style?: React.CSSProperties;
}
