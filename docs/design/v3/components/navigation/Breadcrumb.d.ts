export interface BreadcrumbSegment { label: string; href?: string; }
export interface BreadcrumbProps {
  /** Ordered path; last item is the current page (no link) */
  segments: BreadcrumbSegment[];
}
