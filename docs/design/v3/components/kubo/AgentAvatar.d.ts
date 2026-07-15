/**
 * Agent avatar — image or bot-icon fallback in a muted circle.
 * @startingPoint section="Kubo Patterns" subtitle="Agent avatar" viewport="700x100"
 */
export interface AgentAvatarProps {
  /** An image URL, or undefined for the bot-icon fallback */
  avatar?: string;
  name?: string;
  size?: 'sm' | 'md' | 'lg';
  style?: React.CSSProperties;
}
