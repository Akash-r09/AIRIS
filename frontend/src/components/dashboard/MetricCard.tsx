import type { LucideIcon } from "lucide-react";
import { Card } from "../ui/Card";
import { Badge } from "../ui/Badge";
import { Skeleton } from "../ui/Skeleton";
import { AnimatedCounter } from "../common/AnimatedCounter";
import { TrendBadge } from "../common/TrendBadge";
import { RISK_LEVEL_BADGE_VARIANT, RISK_LEVEL_LABEL } from "../../lib/constants";
import type { MetricCardData } from "../../types/forecast";

type Emphasis = "primary" | "secondary";

interface MetricCardProps {
  data?: MetricCardData;
  icon: LucideIcon;
  emphasis?: Emphasis;
  isLoading?: boolean;
}

export function MetricCard({ data, icon: Icon, emphasis = "secondary", isLoading = false }: MetricCardProps) {
  const isPrimary = emphasis === "primary";

  if (isLoading || !data) {
    return (
      <Card hoverable className={isPrimary ? "p-6" : "p-4"}>
        <Skeleton className={isPrimary ? "h-9 w-9 rounded-xl" : "h-7 w-7 rounded-lg"} />
        <Skeleton className={`mt-4 h-3 w-24 ${isPrimary ? "" : "w-16"}`} />
        <Skeleton className={`mt-3 ${isPrimary ? "h-10 w-32" : "h-6 w-20"}`} />
      </Card>
    );
  }

  return (
    <Card
      hoverable
      className={`
        group
        transition-all
        duration-300

        ${
          isPrimary
            ? "min-h-[170px]"
            : "min-h-[125px]"
        }
      `}
    >
      <div className="flex items-start justify-between">
        <span
          className={`
            flex items-center justify-center
            rounded-xl

            bg-gradient-to-br
            from-blue-500/30
            via-cyan-500/20
            to-blue-700/20

            border border-blue-400/20

            shadow-lg
            shadow-blue-500/20

            text-blue-300

            transition-all
            duration-300

            group-hover:shadow-blue-400/40

            ${isPrimary ? "h-11 w-11" : "h-9 w-9"}
          `}
        >
          <Icon size={isPrimary ? 20 : 16} />
        </span>

        {data.riskLevel && isPrimary && (
          <Badge variant={RISK_LEVEL_BADGE_VARIANT[data.riskLevel]}>{RISK_LEVEL_LABEL[data.riskLevel]}</Badge>
        )}
      </div>

      <p
        className={`
          mt-5
          uppercase
          tracking-[0.15em]
          text-slate-400

          ${isPrimary ? "text-xs" : "text-[11px]"}
        `}
      >
        {data.label}
      </p>

      <AnimatedCounter
        value={data.value}
        suffix={data.unit}
        className={`
        mt-2
        block
        font-bold
        tracking-tight
        text-white

        ${isPrimary ? "text-5xl" : "text-2xl"}
        `}
      />

      <div className="mt-2">
        <TrendBadge direction={data.trend.direction} value={data.trend.value} invert={data.trend.invert} />
      </div>
    </Card>
  );
}
