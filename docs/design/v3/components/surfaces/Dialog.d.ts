/**
 * Centered modal on a dimmed overlay. popover bg, rounded-4xl, ghost close button top-right.
 * @startingPoint section="Surfaces" subtitle="Modal dialog" viewport="700x360"
 */
export interface DialogProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Element that opens the dialog when clicked */
  trigger?: React.ReactNode;
  /** Inter 18px title */
  title?: string;
  description?: string;
  /** Footer actions row (right-aligned) */
  footer?: React.ReactNode;
  showClose?: boolean;
  children?: React.ReactNode;
}
