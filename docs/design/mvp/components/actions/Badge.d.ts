/**
 * Small pill status/label chip. Shares the button's semantic color vocabulary.
 * @startingPoint section="Primitives" subtitle="Pill status chips, 5 variants" viewport="700x100"
 */
export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'secondary' | 'destructive' | 'outline' | 'ghost';
  /** Lucide icon name shown before the label */
  icon?: string;
}
