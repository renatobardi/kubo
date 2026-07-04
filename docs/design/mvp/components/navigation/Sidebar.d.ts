/**
 * The 256px app sidebar — brand header, grouped Lucide nav with active highlight, single-owner footer.
 * @startingPoint section="Navigation" subtitle="256px app sidebar" viewport="280x600"
 */
export interface SidebarNavItem { title: string; icon: string; }
export interface SidebarGroup { label: string; items: SidebarNavItem[]; }
export interface SidebarProps {
  /** Ungrouped items rendered above the labelled groups (e.g. Home) */
  top?: SidebarNavItem[];
  /** Labelled nav groups, in order */
  groups?: SidebarGroup[];
  /** Title of the active nav item */
  active?: string;
  onNavigate?: (title: string) => void;
  user?: { name: string; email: string };
  brand?: string;
  tagline?: string;
}
