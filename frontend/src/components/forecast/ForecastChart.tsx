import { useMemo } from "react";
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import type { ForecastPoint } from "../../types/forecast";

// Hardcoded to the approved palette (design tokens are Tailwind-only;
// Recharts requires literal color values, not utility classes).
const ACCENT = "#38BDF8";
const ACCENT_GLOW = "#7DD3FC";
const GRID_LINE = "rgba(255,255,255,0.06)";
const AXIS_LINE = "rgba(255,255,255,0.12)";
const AXIS_TEXT = "rgba(255,255,255,0.4)";

interface ChartRow {
  timeLabel: string;
  isFuture: boolean;
  observed: number | null;
  predicted: number | null;
  low: number | null;
  bandWidth: number | null;
}

interface ForecastChartProps {
  points: ForecastPoint[];
  height?: number;
}

function formatHour(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString(undefined, { hour: "numeric" });
}

function toChartRows(points: ForecastPoint[]): ChartRow[] {
  const lastObservedIndex = points.reduce((acc, p, i) => (p.observedPm25 !== undefined ? i : acc), -1);

  return points.map((point, index) => {
    const hasBand = point.confidenceLow !== undefined && point.confidenceHigh !== undefined;
    return {
      timeLabel: formatHour(point.timestamp),
      isFuture: point.observedPm25 === undefined,
      observed: point.observedPm25 ?? null,
      // Starts one point early (at lastObservedIndex) so the dashed
      // forecast line visually connects to the solid observed line
      // instead of leaving a gap at the "now" boundary.
      predicted: index >= lastObservedIndex ? point.predictedPm25 : null,
      low: hasBand ? point.confidenceLow! : null,
      bandWidth: hasBand ? point.confidenceHigh! - point.confidenceLow! : null,
    };
  });
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const value = payload.find((p: any) => p.dataKey === "observed" || p.dataKey === "predicted");
  if (!value) return null;

  return (
    <div className="rounded-xl border border-border-subtle bg-secondary/95 px-3 py-2 text-xs shadow-lg backdrop-blur-xl">
      <p className="font-medium text-white/70">{label}</p>
      <p className="mt-1 text-white">
        {value.value} <span className="text-white/50">µg/m³ PM2.5</span>
      </p>
    </div>
  );
}

export function ForecastChart({ points, height = 380 }: ForecastChartProps) {
  const data = useMemo(() => toChartRows(points), [points]);
  const nowIndex = points.findIndex((p) => p.observedPm25 === undefined) - 1;
  const nowLabel = nowIndex >= 0 ? data[nowIndex]?.timeLabel : undefined;

  return (
    <div role="img" aria-label="Line chart of observed and forecast PM2.5 concentration over time">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <defs>

          <linearGradient id="confidenceFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={ACCENT_GLOW} stopOpacity={0.28}/>
            <stop offset="60%" stopColor={ACCENT} stopOpacity={0.12}/>
            <stop offset="100%" stopColor={ACCENT} stopOpacity={0}/>
          </linearGradient>

          <filter id="glow">

          <feGaussianBlur stdDeviation="4" result="coloredBlur"/>

          <feMerge>

          <feMergeNode in="coloredBlur"/>

          <feMergeNode in="SourceGraphic"/>

          </feMerge>

          </filter>

          </defs>

          <CartesianGrid strokeDasharray="3 3" stroke={GRID_LINE} vertical={false} />

          <XAxis
            dataKey="timeLabel"
            tickLine={false}
            axisLine={{ stroke: AXIS_LINE }}
            tick={{ fill: AXIS_TEXT, fontSize: 12 }}
            interval="preserveStartEnd"
            minTickGap={24}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            tick={{ fill: AXIS_TEXT, fontSize: 12 }}
            width={40}
            label={{ value: "µg/m³", angle: -90, position: "insideLeft", fill: AXIS_TEXT, fontSize: 12 }}
          />

          <Tooltip content={<ChartTooltip />} cursor={{ stroke: AXIS_LINE }} />

          <Area
            dataKey="low"
            stackId="confidence"
            stroke="none"
            fill="transparent"
            isAnimationActive={false}
          />
          <Area
            dataKey="bandWidth"
            stackId="confidence"
            stroke="none"
            fill="url(#confidenceFill)"
            isAnimationActive={false}
          />

          {nowLabel && (
            <ReferenceLine
              x={nowLabel}
              stroke={AXIS_LINE}
              strokeDasharray="4 4"
              label={{ value: "Now", fill: AXIS_TEXT, fontSize: 11, position: "insideTopRight" }}
            />
          )}

          <Line
            dataKey="observed"
            stroke={ACCENT}
            strokeWidth={3}
            filter="url(#glow)"
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            dataKey="predicted"
            stroke={ACCENT}
            strokeWidth={3}
            filter="url(#glow)"
            strokeDasharray="8 6"
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
