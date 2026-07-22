import { Thermometer, Droplets, Wind, Gauge } from "lucide-react";
import { Card } from "../ui/Card";
import { SectionHeading } from "../common/SectionHeading";
import { Skeleton } from "../ui/Skeleton";
import { ErrorState } from "../common/ErrorState";
import { EmptyState } from "../common/EmptyState";
import { WeatherCard } from "./WeatherCard";
import type { WeatherSnapshot } from "../../types/forecast";

interface WeatherSidebarProps {
  weather?: WeatherSnapshot;
  isLoading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

function windDirectionLabel(deg: number): string {
  const directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return directions[Math.round(deg / 45) % 8];
}

export function WeatherSidebar({ weather, isLoading = false, error = null, onRetry }: WeatherSidebarProps) {
  return (
    <Card className="p-6">
      <SectionHeading title="Weather" description="Current conditions" />

      {isLoading && (
        <div className="space-y-2.5">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-11 w-full" rounded="rounded-xl" />
          ))}
        </div>
      )}

      {!isLoading && error && <ErrorState message={error} onRetry={onRetry} />}

      {!isLoading && !error && !weather && <EmptyState message="Weather data unavailable." />}

      {!isLoading && !error && weather && (
        <div className="space-y-2.5">
          <WeatherCard icon={Thermometer} label="Temperature" value={`${weather.temperatureC}°C`} />
          <WeatherCard icon={Droplets} label="Humidity" value={`${weather.humidityPercent}%`} />
          <WeatherCard
            icon={Wind}
            label="Wind"
            value={`${weather.windSpeedKph} km/h ${windDirectionLabel(weather.windDirectionDeg)}`}
          />
          <WeatherCard icon={Gauge} label="Pressure" value={`${weather.pressureHpa} hPa`} />
        </div>
      )}
    </Card>
  );
}
