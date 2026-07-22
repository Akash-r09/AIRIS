import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import type { TrendDirection } from "../../types/forecast";

interface TrendBadgeProps {
  direction: TrendDirection;
  value: number;
  /** True when an upward trend represents a worse outcome (e.g. PM2.5, AQI). */
  invert?: boolean;
  unit?: string;
}

const ICONS: Record<TrendDirection, typeof TrendingUp> = {
  up: TrendingUp,
  down: TrendingDown,
  flat: Minus,
};

function resolveColorClass(direction: TrendDirection, invert: boolean): string {
  if (direction === "flat") return "text-white/50";
  const isGood = invert ? direction === "down" : direction === "up";
  return isGood ? "text-success" : "text-danger";
}

export function TrendBadge({ direction, value, invert = false, unit = "%" }: TrendBadgeProps) {
  const Icon = ICONS[direction];
  const colorClass = resolveColorClass(direction, invert);

  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${colorClass}`}>
      <Icon size={14} aria-hidden="true" />
      <span>
        {value > 0 && direction !== "flat" ? "+" : ""}
        {value}
        {unit}
      </span>
    </span>
  );
}
