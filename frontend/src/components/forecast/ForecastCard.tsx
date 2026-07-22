import { Card } from "../ui/Card";
import { SectionHeading } from "../common/SectionHeading";
import { ErrorState } from "../common/ErrorState";
import { EmptyState } from "../common/EmptyState";
import { Skeleton } from "../ui/Skeleton";
import { ForecastChart } from "./ForecastChart";
import { ForecastLegend } from "./ForecastLegend";
import type { ForecastSeries } from "../../types/forecast";

interface ForecastCardProps {
  series?: ForecastSeries;
  isLoading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function ForecastCard({ series, isLoading = false, error = null, onRetry }: ForecastCardProps) {
  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <SectionHeading
          title="Forecast"
          description={
            series ? `${series.horizonHours}-hour PM2.5 outlook, updated ${formatUpdatedAt(series.generatedAt)}` : undefined
          }
        />
        {!isLoading && !error && series && <ForecastLegend />}
      </div>

      {isLoading && <Skeleton className="h-[380px] w-full" rounded="rounded-xl" />}
      {!isLoading && error && <ErrorState message={error} onRetry={onRetry} />}
      {!isLoading && !error && series && series.points.length === 0 && (
        <EmptyState message="No forecast data available for this location yet." />
      )}
      {!isLoading && !error && series && series.points.length > 0 && <ForecastChart points={series.points} />}
    </Card>
  );
}

function formatUpdatedAt(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
