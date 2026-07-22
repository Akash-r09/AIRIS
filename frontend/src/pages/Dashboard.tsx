import { motion } from "framer-motion";
import { Activity, CloudFog, Thermometer, Droplets, Wind, Gauge, Flame } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { DashboardGrid } from "../components/dashboard/DashboardGrid";
import { MetricCard } from "../components/dashboard/MetricCard";
import { ForecastCard } from "../components/forecast/ForecastCard";
import { AIInsights } from "../components/ai/AIInsights";
import { WeatherSidebar } from "../components/weather/WeatherSidebar";
import { ErrorState } from "../components/common/ErrorState";
import { useForecast } from "../hooks/useForecast";
import type { MetricCardData } from "../types/forecast";

const METRIC_ICON: Record<string, LucideIcon> = {
  aqi: Activity,
  "pm25-forecast": CloudFog,
  temperature: Thermometer,
  humidity: Droplets,
  wind: Wind,
  pressure: Gauge,
  wildfire: Flame,
};

const PRIMARY_METRIC_IDS = ["aqi", "pm25-forecast"];
const SECONDARY_METRIC_IDS = ["temperature", "humidity", "wind", "pressure", "wildfire"];

function findMetric(metrics: MetricCardData[] | undefined, id: string): MetricCardData | undefined {
  return metrics?.find((m) => m.id === id);
}

export default function Dashboard() {
  const { data, isLoading, error, refetch } = useForecast();

  return (
    <>
      <h1 className="sr-only">AIRIS Dashboard</h1>

  {/* Dashboard Header */}

  <motion.div
    initial={{ opacity: 0, y: -20 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.45 }}
    className="mx-auto mb-8 flex max-w-[1600px] flex-col justify-between gap-6 px-4 pt-8 sm:px-6 lg:flex-row lg:items-center"
  >
    <div>
      <div className="flex items-center gap-3">

        <div className="flex h-14 w-14 items-center justify-center rounded-2xl
                        bg-gradient-to-br
                        from-cyan-500/20
                        to-blue-600/20
                        border border-cyan-400/20
                        shadow-glow">

          🌍

        </div>

        <div>

          <h1 className="text-4xl font-black tracking-tight
              bg-gradient-to-r
              from-cyan-300
              via-blue-400
              to-indigo-400
              bg-clip-text
              text-transparent">
            AIRIS
          </h1>

          <p className="mt-1 text-slate-400">
            AI Environmental Intelligence Platform
          </p>

        </div>

      </div>

      <p className="mt-5 max-w-xl text-sm leading-7 text-slate-400">
        Real-time environmental monitoring, PM2.5 forecasting,
        AI-powered insights and weather intelligence.
      </p>

    </div>

    <div className="flex flex-wrap items-center gap-3">

      <div className="rounded-2xl border border-emerald-500/20
                      bg-emerald-500/10
                      px-4 py-3">

        <p className="text-xs uppercase tracking-[0.2em] text-emerald-300">
          System
        </p>

        <div className="mt-1 flex items-center gap-2">

          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse"/>

          <span className="font-semibold text-white">
            Connected
          </span>

        </div>

      </div>

      <div className="rounded-2xl border border-white/10
                      bg-white/5
                      px-4 py-3">

        <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
          Updated
        </p>

        <p className="mt-1 font-semibold text-white">
          {new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>

      </div>

    </div>

  </motion.div>

      <DashboardGrid
        metrics={
          <MetricsSection metrics={data?.metrics} isLoading={isLoading} error={error} onRetry={refetch} />
        }
        content={
          <>
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
              <ForecastCard series={data?.forecast} isLoading={isLoading} error={error} onRetry={refetch} />
            </motion.div>
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.1 }}
            >
              <AIInsights insight={data?.aiInsight} isLoading={isLoading} error={error} onRetry={refetch} />
            </motion.div>
          </>
        }
        weather={
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.15 }}
          >
            <WeatherSidebar weather={data?.weather} isLoading={isLoading} error={error} onRetry={refetch} />
          </motion.div>
        }
      />
    </>
  );
}

interface MetricsSectionProps {
  metrics?: MetricCardData[];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
}

function MetricsSection({ metrics, isLoading, error, onRetry }: MetricsSectionProps) {
  if (!isLoading && error) {
    return <ErrorState message={error} onRetry={onRetry} className="rounded-2xl border border-border-subtle bg-surface" />;
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {PRIMARY_METRIC_IDS.map((id) => (
          <MetricCard
            key={id}
            data={findMetric(metrics, id)}
            icon={METRIC_ICON[id]}
            emphasis="primary"
            isLoading={isLoading}
          />
        ))}
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        {SECONDARY_METRIC_IDS.map((id) => (
          <MetricCard
            key={id}
            data={findMetric(metrics, id)}
            icon={METRIC_ICON[id]}
            emphasis="secondary"
            isLoading={isLoading}
          />
        ))}
      </div>
    </div>
  );
}
