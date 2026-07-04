/**
 * Kubo logo. The mark is the kanji 智 (chi = wisdom/intellect) in a tile beside the "Kubo" wordmark.
 * @startingPoint section="Brand" subtitle="Kanji mark + full lockup" viewport="700x120"
 */
export interface LogoProps {
  /** full = tile + "Kubo" (+tagline) · mark = 智 tile only · kanji = 智 typographic */
  variant?: 'full' | 'mark' | 'kanji';
  /** Base pixel size (tile edge for full/mark; font-size for kanji) */
  size?: number;
  /** Override wordmark color (defaults to foreground) */
  color?: string;
  onDark?: boolean;
  /** Tagline under the wordmark in the full lockup; pass '' to hide */
  tagline?: string;
  /** Kanji shown in the tile mark. Default 智 (wisdom/intellect). */
  markGlyph?: string;
  style?: React.CSSProperties;
}
