// Shared domain types for AIRIS. Kept independent of any single backend
// response shape — lib/api.ts is responsible for mapping raw API payloads
// into these types, so a backend contract change only touches api.ts.

export type TrendDirection = "up" | "down" | "flat";

export type StatusLevel = "operational" | "degraded" | "offline";

export type RiskLevel = "low" | "moderate" | "high" | "severe";

export interface ForecastPoint {
  /** ISO 8601 timestamp for this forecast sample. */
  timestamp: string;
  /** Observed PM2.5 (µg/m³), absent for future/forecast-only points. */
  observedPm25?: number;
  /** Model-predicted PM2.5 (µg/m³). */
  predictedPm25: number;
  /** Optional prediction interval bounds, if the model provides them. */
  confidenceLow?: number;
  confidenceHigh?: number;
}

export interface ForecastSeries {
  points: ForecastPoint[];
  generatedAt: string;
  horizonHours: number;
}

export interface MetricCardData {
  id: string;
  label: string;
  value: number;
  unit: string;
  trend: {
    direction: TrendDirection;
    value: number;
    /** True when an upward trend is bad (e.g. AQI, PM2.5, wildfire risk). */
    invert: boolean;
  };
  riskLevel?: RiskLevel;
}

export interface WeatherSnapshot {
  temperatureC: number;
  humidityPercent: number;
  windSpeedKph: number;
  windDirectionDeg: number;
  pressureHpa: number;
}

export interface AIRecommendation {
  id: string;
  title: string;
  description: string;
  severity: RiskLevel;
}

export interface AIInsight {
  forecastSummary: string;
  observedTrend: string;
  confidencePercent: number;
  possibleCauses: string[];
  recommendations: AIRecommendation[];
}

export interface DashboardSnapshot {
  metrics: MetricCardData[];
  forecast: ForecastSeries;
  weather: WeatherSnapshot;
  aiInsight: AIInsight;
  systemStatus: StatusLevel;
}
