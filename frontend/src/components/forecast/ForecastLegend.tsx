const LEGEND_ITEMS = [
  { label: "Observed", swatch: "h-0.5 w-4 bg-accent" },
  { label: "Forecast", swatch: "h-0.5 w-4 border-t-2 border-dashed border-accent" },
  { label: "Confidence range", swatch: "h-2.5 w-4 rounded-sm bg-accent/20" },
];

export function ForecastLegend() {
  return (
    <ul className="flex flex-wrap items-center gap-4 text-xs text-white/50" aria-label="Chart legend">
      {LEGEND_ITEMS.map((item) => (
        <li key={item.label} className="flex items-center gap-2">
          <span className={item.swatch} aria-hidden="true" />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
