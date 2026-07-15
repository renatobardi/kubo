/**
 * Dashboard stat tile — big number + label + muted icon square.
 * @startingPoint section="Kubo Patterns" subtitle="Dashboard stat tile" viewport="320x100"
 */
export interface StatTileProps {
  label: string;
  value: number | string;
  /** Lucide icon name */
  icon: string;
  onClick?: () => void;
  style?: React.CSSProperties;
}
