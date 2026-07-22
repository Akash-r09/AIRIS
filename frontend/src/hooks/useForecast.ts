import { useCallback, useEffect, useState } from "react";
import type { DashboardSnapshot } from "../types/forecast";
import { api, ApiError } from "../lib/api";

type RequestStatus = "loading" | "success" | "error";

interface ForecastState {
  status: RequestStatus;
  data?: DashboardSnapshot;
  error?: string;
}

/**
 * Owns dashboard data loading. Fetches the assembled DashboardSnapshot
 * from the backend's GET /dashboard endpoint through the shared api
 * client (lib/api.ts) — no fetch() calls live here or anywhere else.
 * The backend returns data already shaped to match DashboardSnapshot
 * exactly, so no transformation happens in this hook or in any
 * consuming component.
 */
export function useForecast() {
  const [state, setState] = useState<ForecastState>({ status: "loading" });

  const load = useCallback(() => {
    let cancelled = false;
    setState({ status: "loading" });

    api
      .get<DashboardSnapshot>("/dashboard")
      .then((data) => {
        if (!cancelled) setState({ status: "success", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.message : "Failed to load dashboard data.";
        setState({ status: "error", error: message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load]);

  return {
    status: state.status,
    data: state.data,
    error: state.error ?? null,
    isLoading: state.status === "loading",
    refetch: load,
  };
}
