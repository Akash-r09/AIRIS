import type { DashboardSnapshot, ForecastPoint } from "../types/forecast";

// Deterministic diurnal PM2.5 curve: a function of hour-of-day plus a
// mild upward drift into the forecast window. No randomness — the same
// timestamp always produces the same value.
const BASE_PM25 = 34;
const AMPLITUDE = 14;

function buildForecastPoints(): ForecastPoint[] {
  const anchor = new Date();
  anchor.setMinutes(0, 0, 0);

  const points: ForecastPoint[] = [];

  for (let offset = -11; offset <= 12; offset++) {
    const timestamp = new Date(anchor.getTime() + offset * 60 * 60 * 1000);
    const diurnal = Math.sin((timestamp.getHours() / 24) * Math.PI * 2 - Math.PI / 2);
    const drift = offset > 0 ? offset * 0.6 : 0;
    const predictedPm25 = Math.max(6, Math.round(BASE_PM25 + diurnal * AMPLITUDE + drift));

    points.push({
      timestamp: timestamp.toISOString(),
      observedPm25: offset <= 0 ? Math.max(6, predictedPm25 - 2) : undefined,
      predictedPm25,
      confidenceLow: offset >= 0 ? Math.max(4, predictedPm25 - 6) : undefined,
      confidenceHigh: offset >= 0 ? predictedPm25 + 6 : undefined,
    });
  }

  return points;
}

export const MOCK_DASHBOARD_SNAPSHOT: DashboardSnapshot = {
  systemStatus: "operational",
  metrics: [
    {
      id: "aqi",
      label: "Current AQI",
      value: 96,
      unit: "",
      trend: { direction: "up", value: 8, invert: true },
      riskLevel: "moderate",
    },
    {
      id: "pm25-forecast",
      label: "PM2.5 Forecast (1h)",
      value: 42,
      unit: " µg/m³",
      trend: { direction: "up", value: 5, invert: true },
      riskLevel: "moderate",
    },
    {
      id: "temperature",
      label: "Temperature",
      value: 29,
      unit: "°C",
      trend: { direction: "flat", value: 0, invert: false },
    },
    {
      id: "humidity",
      label: "Humidity",
      value: 58,
      unit: "%",
      trend: { direction: "down", value: 3, invert: false },
    },
    {
      id: "wind",
      label: "Wind Speed",
      value: 12,
      unit: " km/h",
      trend: { direction: "up", value: 2, invert: false },
    },
    {
      id: "pressure",
      label: "Pressure",
      value: 1008,
      unit: " hPa",
      trend: { direction: "down", value: 1, invert: false },
    },
    {
      id: "wildfire",
      label: "Wildfire Risk",
      value: 3,
      unit: "/5",
      trend: { direction: "up", value: 1, invert: true },
      riskLevel: "high",
    },
  ],
  forecast: {
    points: buildForecastPoints(),
    generatedAt: new Date().toISOString(),
    horizonHours: 12,
  },
  weather: {
    temperatureC: 29,
    humidityPercent: 58,
    windSpeedKph: 12,
    windDirectionDeg: 210,
    pressureHpa: 1008,
  },
  aiInsight: {
    forecastSummary:
      "PM2.5 concentrations are expected to climb through the afternoon, peaking near 52 µg/m³ around 4 PM before easing overnight as wind speeds recover.",
    observedTrend:
      "Pollution levels have risen 18% over the past 6 hours, tracking closely with falling wind speed and rising surface temperature.",
    confidencePercent: 87,
    possibleCauses: [
      "Reduced wind dispersion during afternoon hours",
      "Elevated fire activity detected upwind within 40km",
      "Temperature inversion trapping ground-level particulates",
    ],
    recommendations: [
      {
        id: "n95",
        title: "Wear an N95 mask outdoors",
        description: "Air quality is forecast to reach unhealthy-for-sensitive-groups levels by mid-afternoon.",
        severity: "moderate",
      },
      {
        id: "exercise",
        title: "Avoid outdoor exercise",
        description: "Postpone strenuous outdoor activity until after 8 PM when levels are forecast to improve.",
        severity: "moderate",
      },
      {
        id: "windows",
        title: "Keep windows closed",
        description: "Indoor air quality will stay meaningfully better than outdoor levels through the afternoon.",
        severity: "low",
      },
      {
        id: "transit",
        title: "Use public transport",
        description: "Reduce vehicle emissions during the forecast peak window to support faster regional recovery.",
        severity: "low",
      },
    ],
  },
};
