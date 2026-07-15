/**
 * Unified page header — Inter h1 + muted description + right actions + separator.
 * @startingPoint section="Navigation" subtitle="Page title block" viewport="700x120"
 */
export interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}
