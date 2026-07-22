import type { ReactNode } from "react";

interface DashboardGridProps {
  metrics: ReactNode;
  content: ReactNode;
  weather: ReactNode;
}

/**
 * Structural shell for the dashboard. Owns spacing and the responsive
 * grid only — content is injected via composition, so this component
 * never needs to know what a metric card, chart, or AI panel renders.
 *
 * Layout: metrics band full-width up top, then a 12-col row where the
 * main content column (chart / AI insights) takes 8/12 and the weather
 * sidebar takes 4/12. On narrower screens both collapse to a single
 * column and stack in DOM order — content first, weather last — which
 * matches the approved reading order without needing separate mobile
 * markup.
 */
export function DashboardGrid({ metrics, content, weather }: DashboardGridProps) {
  return (
    <div className="mx-auto max-w-[1600px] space-y-8 px-4 py-6 sm:px-6 md:py-8">
      <section aria-label="Key metrics">{metrics}</section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
        <div className="space-y-6 xl:col-span-8">{content}</div>
        <div className="xl:col-span-4 xl:sticky xl:top-24 xl:self-start">{weather}</div>
      </div>
    </div>
  );
}
