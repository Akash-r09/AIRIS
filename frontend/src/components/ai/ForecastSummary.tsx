interface ForecastSummaryProps {
  summary: string;
  observedTrend: string;
}

export function ForecastSummary({ summary, observedTrend }: ForecastSummaryProps) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-white/40">Forecast Summary</h3>
        <p className="mt-1.5 text-sm leading-relaxed text-white/80">{summary}</p>
      </div>
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-white/40">Observed Trend</h3>
        <p className="mt-1.5 text-sm leading-relaxed text-white/80">{observedTrend}</p>
      </div>
    </div>
  );
}
