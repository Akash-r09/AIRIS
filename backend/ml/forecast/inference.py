"""
AIRIS - Forecast Inference
Loads the trained PM2.5 forecast model and produces next-hour predictions
from a feature row (or DataFrame of rows).
"""

import os
import joblib
import numpy as np
import pandas as pd

DEFAULT_MODEL_PATH = "backend/ml/artifacts/pm25_forecast_model.joblib"


class PM25ForecastModel:
    """Thin wrapper around the persisted joblib artifact."""

    def __init__(self, model_path: str = DEFAULT_MODEL_PATH):
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Model artifact not found at: {model_path}. Run train.py first."
            )
        artifact = joblib.load(model_path)
        self.model = artifact["model"]
        self.feature_columns = artifact["feature_columns"]
        self.target_column = artifact.get("target_column", "target_pm25_next_1h")
        self.model_path = model_path

    def _prepare_input(self, data) -> pd.DataFrame:
        if isinstance(data, dict):
            df = pd.DataFrame([data])
        elif isinstance(data, pd.Series):
            df = data.to_frame().T
        elif isinstance(data, pd.DataFrame):
            df = data.copy()
        else:
            raise TypeError(
                f"Unsupported input type for prediction: {type(data)}"
            )

        missing = [c for c in self.feature_columns if c not in df.columns]
        if missing:
            raise ValueError(
                f"Missing required feature columns for inference: {missing}"
            )

        df = df[self.feature_columns]
        df = df.apply(pd.to_numeric, errors="coerce")

        if df.isnull().any().any():
            df = df.fillna(df.median(numeric_only=True))
            df = df.fillna(0)

        return df

    def predict(self, data) -> dict:
        """
        Predict next-hour PM2.5 for a single record (dict / Series) or
        a batch (DataFrame).

        Returns a dict for single-record input, or a list of dicts for
        batch input.
        """
        X = self._prepare_input(data)
        preds = self.model.predict(X)

        if isinstance(data, pd.DataFrame) and len(data) > 1:
            results = []
            for i, pred in enumerate(preds):
                results.append(
                    {
                        "predicted_pm25_next_1h": float(pred),
                        "row_index": int(i),
                        "model": "HistGradientBoostingRegressor",
                    }
                )
            return {"predictions": results, "count": len(results)}

        return {
            "predicted_pm25_next_1h": float(preds[0]),
            "model": "HistGradientBoostingRegressor",
            "model_path": self.model_path,
        }


_model_singleton = None


def get_model(model_path: str = DEFAULT_MODEL_PATH) -> PM25ForecastModel:
    """Cached loader so repeated inference calls don't reload from disk."""
    global _model_singleton
    if _model_singleton is None or _model_singleton.model_path != model_path:
        _model_singleton = PM25ForecastModel(model_path=model_path)
    return _model_singleton


def predict_next_hour(feature_row: dict, model_path: str = DEFAULT_MODEL_PATH) -> dict:
    """Convenience function: predict PM2.5 for the next hour from a single
    feature dictionary (e.g. the most recent row produced by
    feature_engineering.py)."""
    model = get_model(model_path)
    return model.predict(feature_row)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="AIRIS PM2.5 inference")
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument(
        "--input-json",
        required=True,
        help="Path to a JSON file containing a single feature record",
    )
    args = parser.parse_args()

    with open(args.input_json, "r") as f:
        record = json.load(f)

    result = predict_next_hour(record, model_path=args.model)
    print(json.dumps(result, indent=2))
