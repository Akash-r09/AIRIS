"""
AIRIS - Dashboard API

Exposes GET /dashboard, assembling the DashboardSnapshot the frontend
expects (see frontend/src/types/forecast.ts). Reuses the existing
inference module — the same predict_next_hour() that backs POST
/forecast — for the one real ML value in the response (the PM2.5
forecast metric). Every other field is deterministic placeholder data,
structured field-for-field like frontend/src/lib/mockData.ts, until the
backend services that will eventually produce it (AQI computation, a
live weather feed, real AI-generated analysis) exist.

This module does not modify or import from routes_forecast.py — it only
reuses backend/ml/forecast/inference.py, the same shared dependency.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.ml.forecast.inference import predict_next_hour

router = APIRouter()

# Same verified output path used throughout the ML pipeline (see
# data_pipeline/features/feature_engineering.py's default output).
FEATURES_CSV_PATH = Path("data_pipeline/data/features/features.csv")

RiskLevel = Literal["low", "moderate", "high", "severe"]
TrendDirection = Literal["up", "down", "flat"]
StatusLevel = Literal["operational", "degraded", "offline"]


# ---------------------------------------------------------------------------
# Response models. Field aliases match frontend/src/types/forecast.ts
# (camelCase) exactly. FastAPI serializes response_model output with
# by_alias=True by default, so these produce the exact JSON shape the
# frontend's DashboardSnapshot type expects.
# ---------------------------------------------------------------------------


class TrendModel(BaseModel):
    direction: TrendDirection
    value: float
    invert: bool


class MetricCardModel(BaseModel):
    id: str
    label: str
    value: float
    unit: str
    trend: TrendModel
    risk_level: Optional[RiskLevel] = Field(default=None, alias="riskLevel")

    model_config = ConfigDict(populate_by_name=True)


class ForecastPointModel(BaseModel):
    timestamp: str
    observed_pm25: Optional[float] = Field(default=None, alias="observedPm25")
    predicted_pm25: float = Field(alias="predictedPm25")
    confidence_low: Optional[float] = Field(default=None, alias="confidenceLow")
    confidence_high: Optional[float] = Field(default=None, alias="confidenceHigh")

    model_config = ConfigDict(populate_by_name=True)


class ForecastSeriesModel(BaseModel):
    points: List[ForecastPointModel]
    generated_at: str = Field(alias="generatedAt")
    horizon_hours: int = Field(alias="horizonHours")

    model_config = ConfigDict(populate_by_name=True)


class WeatherSnapshotModel(BaseModel):
    temperature_c: float = Field(alias="temperatureC")
    humidity_percent: float = Field(alias="humidityPercent")
    wind_speed_kph: float = Field(alias="windSpeedKph")
    wind_direction_deg: float = Field(alias="windDirectionDeg")
    pressure_hpa: float = Field(alias="pressureHpa")

    model_config = ConfigDict(populate_by_name=True)


class AIRecommendationModel(BaseModel):
    id: str
    title: str
    description: str
    severity: RiskLevel


class AIInsightModel(BaseModel):
    forecast_summary: str = Field(alias="forecastSummary")
    observed_trend: str = Field(alias="observedTrend")
    confidence_percent: float = Field(alias="confidencePercent")
    possible_causes: List[str] = Field(alias="possibleCauses")
    recommendations: List[AIRecommendationModel]

    model_config = ConfigDict(populate_by_name=True)


class DashboardSnapshotModel(BaseModel):
    metrics: List[MetricCardModel]
    forecast: ForecastSeriesModel
    weather: WeatherSnapshotModel
    ai_insight: AIInsightModel = Field(alias="aiInsight")
    system_status: StatusLevel = Field(alias="systemStatus")

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Real ML integration
# ---------------------------------------------------------------------------


def _get_real_pm25_forecast() -> float:
    """
    Sources a feature row from the most recent record produced by the
    feature engineering pipeline (features.csv) and runs it through
    predict_next_hour() — the identical function that backs POST
    /forecast. This is the single live-model value in the dashboard
    snapshot; nothing about the inference path is duplicated here.
    """
    if not FEATURES_CSV_PATH.exists():
        raise FileNotFoundError(
            f"Feature dataset not found at {FEATURES_CSV_PATH}. "
            "Run the feature engineering pipeline before requesting /dashboard."
        )

    df = pd.read_csv(FEATURES_CSV_PATH)
    if df.empty:
        raise ValueError(f"Feature dataset at {FEATURES_CSV_PATH} is empty.")

    latest_row = df.iloc[-1].to_dict()
    result = predict_next_hour(latest_row)
    return float(result["predicted_pm25_next_1h"])


# ---------------------------------------------------------------------------
# Deterministic placeholder data — mirrors frontend/src/lib/mockData.ts
# field-for-field so the dashboard renders identically to the approved
# mock until the corresponding backend services exist. No Math.random()
# equivalent used; the forecast curve is a pure function of the hour.
# ---------------------------------------------------------------------------

BASE_PM25 = 34.0
AMPLITUDE = 14.0


def _build_forecast_points() -> List[ForecastPointModel]:
    anchor = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    points: List[ForecastPointModel] = []

    for offset in range(-11, 13):
        timestamp = anchor + timedelta(hours=offset)
        diurnal = math.sin((timestamp.hour / 24) * math.pi * 2 - math.pi / 2)
        drift = offset * 0.6 if offset > 0 else 0.0
        predicted = max(6.0, round(BASE_PM25 + diurnal * AMPLITUDE + drift, 1))

        points.append(
            ForecastPointModel(
                timestamp=timestamp.isoformat(),
                observedPm25=max(6.0, round(predicted - 2, 1)) if offset <= 0 else None,
                predictedPm25=predicted,
                confidenceLow=max(4.0, round(predicted - 6, 1)) if offset >= 0 else None,
                confidenceHigh=round(predicted + 6, 1) if offset >= 0 else None,
            )
        )

    return points


def _placeholder_metrics(pm25_forecast_value: float) -> List[MetricCardModel]:
    return [
        MetricCardModel(
            id="aqi",
            label="Current AQI",
            value=96,
            unit="",
            trend=TrendModel(direction="up", value=8, invert=True),
            riskLevel="moderate",
        ),
        MetricCardModel(
            id="pm25-forecast",
            label="PM2.5 Forecast (1h)",
            value=round(pm25_forecast_value, 1),
            unit=" µg/m³",
            trend=TrendModel(direction="up", value=5, invert=True),
            riskLevel="moderate",
        ),
        MetricCardModel(
            id="temperature",
            label="Temperature",
            value=29,
            unit="°C",
            trend=TrendModel(direction="flat", value=0, invert=False),
        ),
        MetricCardModel(
            id="humidity",
            label="Humidity",
            value=58,
            unit="%",
            trend=TrendModel(direction="down", value=3, invert=False),
        ),
        MetricCardModel(
            id="wind",
            label="Wind Speed",
            value=12,
            unit=" km/h",
            trend=TrendModel(direction="up", value=2, invert=False),
        ),
        MetricCardModel(
            id="pressure",
            label="Pressure",
            value=1008,
            unit=" hPa",
            trend=TrendModel(direction="down", value=1, invert=False),
        ),
        MetricCardModel(
            id="wildfire",
            label="Wildfire Risk",
            value=3,
            unit="/5",
            trend=TrendModel(direction="up", value=1, invert=True),
            riskLevel="high",
        ),
    ]


def _placeholder_weather() -> WeatherSnapshotModel:
    return WeatherSnapshotModel(
        temperatureC=29,
        humidityPercent=58,
        windSpeedKph=12,
        windDirectionDeg=210,
        pressureHpa=1008,
    )


def _placeholder_ai_insight() -> AIInsightModel:
    return AIInsightModel(
        forecastSummary=(
            "PM2.5 concentrations are expected to climb through the afternoon, peaking near "
            "52 \u00b5g/m\u00b3 around 4 PM before easing overnight as wind speeds recover."
        ),
        observedTrend=(
            "Pollution levels have risen 18% over the past 6 hours, tracking closely with "
            "falling wind speed and rising surface temperature."
        ),
        confidencePercent=87,
        possibleCauses=[
            "Reduced wind dispersion during afternoon hours",
            "Elevated fire activity detected upwind within 40km",
            "Temperature inversion trapping ground-level particulates",
        ],
        recommendations=[
            AIRecommendationModel(
                id="n95",
                title="Wear an N95 mask outdoors",
                description="Air quality is forecast to reach unhealthy-for-sensitive-groups levels by mid-afternoon.",
                severity="moderate",
            ),
            AIRecommendationModel(
                id="exercise",
                title="Avoid outdoor exercise",
                description="Postpone strenuous outdoor activity until after 8 PM when levels are forecast to improve.",
                severity="moderate",
            ),
            AIRecommendationModel(
                id="windows",
                title="Keep windows closed",
                description="Indoor air quality will stay meaningfully better than outdoor levels through the afternoon.",
                severity="low",
            ),
            AIRecommendationModel(
                id="transit",
                title="Use public transport",
                description="Reduce vehicle emissions during the forecast peak window to support faster regional recovery.",
                severity="low",
            ),
        ],
    )


@router.get("/dashboard", response_model=DashboardSnapshotModel)
def get_dashboard() -> DashboardSnapshotModel:
    """
    Assembles the full DashboardSnapshot. The PM2.5 forecast metric is
    sourced from the real trained model via predict_next_hour(); every
    other field is deterministic placeholder data pending the services
    that will eventually produce it.
    """
    try:
        pm25_forecast_value = _get_real_pm25_forecast()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return DashboardSnapshotModel(
        metrics=_placeholder_metrics(pm25_forecast_value),
        forecast=ForecastSeriesModel(
            points=_build_forecast_points(),
            generatedAt=datetime.now(timezone.utc).isoformat(),
            horizonHours=12,
        ),
        weather=_placeholder_weather(),
        aiInsight=_placeholder_ai_insight(),
        systemStatus="operational",
    )
