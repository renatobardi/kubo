/**
 * Agent avatar — emoji, image, or bot-icon fallback in a muted circle.
 * @startingPoint section="Kubo Patterns" subtitle="Agent avatar" viewport="700x100"
 */
export interface AgentAvatarProps {
  /** A preset emoji (see assets/agent-emojis.js), an image URL, or undefined for bot fallback */
  avatar?: string;
  name?: string;
  size?: 'sm' | 'md' | 'lg';
  style?: React.CSSProperties;
}
