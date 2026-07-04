/**
 * Pill select with popover menu. bg-input/30, chevron, checkmark on selected.
 * @startingPoint section="Forms" subtitle="Dropdown select" viewport="700x120"
 */
export interface SelectProps {
  options: (string | { value: string; label: string })[];
  value?: string;
  defaultValue?: string;
  placeholder?: string;
  onValueChange?: (value: string) => void;
  size?: 'default' | 'sm';
  disabled?: boolean;
  style?: React.CSSProperties;
}
