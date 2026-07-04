/**
 * Clickable agent card — avatar + name + description, hover lifts border to amber.
 * @startingPoint section="Kubo Patterns" subtitle="Agent picker card" viewport="360x160"
 */
export interface AgentCardProps {
  agent?: { name?: string; description?: string; avatar?: string };
  onClick?: () => void;
  style?: React.CSSProperties;
}
