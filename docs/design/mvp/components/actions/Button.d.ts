/**
 * The signature Kubo button — pill-shaped, sinks 1px on press.
 * @startingPoint section="Primitives" subtitle="Pill button, 6 variants, 8 sizes" viewport="700x140"
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** default = solid amber · outline · secondary · ghost · destructive (tinted, never solid) · link */
  variant?: 'default' | 'outline' | 'secondary' | 'ghost' | 'destructive' | 'link';
  /** default(36) · sm(32) · xs(24) · lg(40) · icon(36) · icon-sm · icon-xs · icon-lg */
  size?: 'default' | 'sm' | 'xs' | 'lg' | 'icon' | 'icon-sm' | 'icon-xs' | 'icon-lg';
  /** Lucide icon name shown before the label (or the glyph for icon sizes) */
  icon?: string;
  /** Lucide icon name shown after the label */
  iconEnd?: string;
}
