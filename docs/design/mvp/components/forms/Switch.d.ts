/**
 * Pill toggle. Track 32×18.4 (sm 24×14), amber when on.
 * @startingPoint section="Forms" subtitle="On/off toggle" viewport="700x90"
 */
export interface SwitchProps {
  checked?: boolean;
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  size?: 'default' | 'sm';
  disabled?: boolean;
  style?: React.CSSProperties;
}
