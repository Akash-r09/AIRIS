import { AlertCircle } from "lucide-react";
import { Card } from "../ui/Card";
import { SectionHeading } from "../common/SectionHeading";
import { Skeleton } from "../ui/Skeleton";
import { ErrorState } from "../common/ErrorState";
import { EmptyState } from "../common/EmptyState";
import { ForecastSummary } from "./ForecastSummary";
import { RecommendationCard } from "./RecommendationCard";
import type { AIInsight } from "../../types/forecast";

interface AIInsightsProps {
  insight?: AIInsight;
  isLoading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

function confidenceTone(percent: number): { bar: string; text: string } {
  if (percent >= 80) return { bar: "bg-success", text: "text-success" };
  if (percent >= 50) return { bar: "bg-warning", text: "text-warning" };
  return { bar: "bg-danger", text: "text-danger" };
}

export function AIInsights({ insight, isLoading = false, error = null, onRetry }: AIInsightsProps) {
  return (
    <Card className="p-8 border border-cyan-400/10 shadow-glow">
      <SectionHeading title="AI Insights" description="Model-generated analysis of current conditions" />

      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="mt-2 h-16 w-full" rounded="rounded-xl" />
        </div>
      )}

      {!isLoading && error && <ErrorState message={error} onRetry={onRetry} />}

      {!isLoading && !error && !insight && <EmptyState message="AI analysis is not available yet." />}

      {!isLoading && !error && insight && (
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <ForecastSummary summary={insight.forecastSummary} observedTrend={insight.observedTrend} />

            <div>
              <h3 className="text-xs font-bold uppercase tracking-[0.18em] text-slate-400">Likely Causes</h3>
              <ul className="mt-2 space-y-1.5">
                {insight.possibleCauses.map((cause) => (
                  <li key={cause} className="flex items-start gap-2 text-sm text-white/70">
                    <AlertCircle className="mt-0.5 shrink-0 text-white/30" size={14} aria-hidden="true" />
                    {cause}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white/40">Confidence</h3>
            <p className={`mt-2 text-5xl font-semibold ${confidenceTone(insight.confidencePercent).text}`}>
              {insight.confidencePercent}%
            </p>
            <div
              className="mt-3 h-2.5 w-full overflow-hidden rounded-full bg-white/5"
              role="progressbar"
              aria-valuenow={insight.confidencePercent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Model confidence"
            >
              <div
                className={`h-full rounded-full ${confidenceTone(insight.confidencePercent).bar}`}
                style={{ width: `${insight.confidencePercent}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-white/40">Based on recent model backtesting accuracy</p>
          </div>

          <div className="lg:col-span-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white/40">Recommended Actions</h3>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {insight.recommendations.map((rec) => (
                <RecommendationCard key={rec.id} recommendation={rec} />
              ))}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
