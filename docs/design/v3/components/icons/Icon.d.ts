/**
 * Lucide icon wrapper (the app's icon system). 16px default, stroke 2.
 * @startingPoint section="Primitives" subtitle="Lucide glyphs at 16px / stroke 2" viewport="700x120"
 */
export interface IconProps {
  /** Lucide glyph name, e.g. "bot", "message-square", "workflow", "plus", "x", "chevron-down" */
  name: string;
  /** Pixel size. 16 default; 12 inside badges/xs buttons; 20 for lg avatars */
  size?: number;
  style?: React.CSSProperties;
}
