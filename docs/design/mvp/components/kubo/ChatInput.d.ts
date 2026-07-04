/**
 * Chat composer — rounded-2xl card, attach button, auto-growing textarea, amber send button.
 * @startingPoint section="Kubo Patterns" subtitle="Chat composer" viewport="700x140"
 */
export interface ChatInputProps {
  onSend?: (content: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Show the "Enter to send" hint line */
  hint?: boolean;
}
