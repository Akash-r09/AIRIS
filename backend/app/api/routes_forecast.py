"""
AIRIS - Forecast API

Exposes POST /forecast. This module is a thin FastAPI adapter around the
existing, already-verified inference pipeline in
backend/ml/forecast/inference.py — no prediction logic is duplicated here.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.ml.forecast.inference import predict_next_hour

router = APIRouter()


class ForecastRequest(BaseModel):
    """
    Feature values for the record to forecast from, keyed by the same
    column names produced by data_pipeline/features/feature_engineering.py
    (e.g. "hour", "hour_sin", "pm25_lag_1h", "pm25_roll_mean_3h", ...).

    The exact set of required keys is whatever `feature_columns` the
    trained model artifact (backend/ml/artifacts/pm25_forecast_model.joblib)
    was fit on — it is intentionally not hardcoded here, so this endpoint
    keeps working as the feature set evolves. Missing/unknown keys are
    reported by the existing inference layer's own validation.
    """

    features: Dict[str, Any] = Field(
        ...,
        description="Feature name -> value mapping matching the trained model's feature_columns.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "features": {
                        "hour": 14,
                        "weekday": 2,
                        "month": 7,
                        "weekend": 0,
                        "hour_sin": 0.5,
                        "hour_cos": 0.87,
                        "pm25_lag_1h": 32.1,
                        "pm25_roll_mean_3h": 30.4,
                    }
                }
            ]
        }
    }


class ForecastResponse(BaseModel):
    predicted_pm25_next_1h: float
    model: str
    model_path: str


@router.post("/forecast", response_model=ForecastResponse)
def forecast(request: ForecastRequest) -> ForecastResponse:
    """Predict next-hour PM2.5 from a single feature record."""
    try:
        result = predict_next_hour(request.features)
    except FileNotFoundError as exc:
        # Model artifact missing / not trained yet.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        # Request is missing required feature columns.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ForecastResponse(**result)
