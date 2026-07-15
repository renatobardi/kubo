/**
 * Loading placeholder (muted, pulsing, rounded-xl) and dark bubble Tooltip.
 * @startingPoint section="Surfaces" subtitle="Skeleton + tooltip" viewport="700x140"
 */
export interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {}
export interface TooltipProps {
  /** Text shown in the dark bubble */
  label: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
  children: React.ReactNode;
}
export function Tooltip(props: TooltipProps): JSX.Element;
