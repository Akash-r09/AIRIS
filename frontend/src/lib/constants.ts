import type { RiskLevel } from "../types/forecast";

export const APP_NAME = "AIRIS";

export const APP_TAGLINE = "AI Environmental Intelligence Platform";

export const ROUTES = {
  landing: "/",
  dashboard: "/dashboard",
} as const;

export const NAV_LINKS: { label: string; to: string }[] = [
  { label: "Dashboard", to: ROUTES.dashboard },
];

export const RISK_LEVEL_LABEL: Record<RiskLevel, string> = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  severe: "Severe",
};

export const RISK_LEVEL_BADGE_VARIANT: Record<RiskLevel, "success" | "warning" | "danger"> = {
  low: "success",
  moderate: "warning",
  high: "danger",
  severe: "danger",
};
