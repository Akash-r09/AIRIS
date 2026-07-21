"""
AIRIS - Feature Attribution
Explains individual PM2.5 predictions using SHAP when available, and
automatically falls back to permutation importance otherwise.
"""

import os
import joblib
import numpy as np
import pandas as pd

from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

DEFAULT_MODEL_PATH = "backend/ml/artifacts/pm25_forecast_model.joblib"

try:
    import shap  # type: ignore
    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False


class FeatureAttributor:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH):
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Model artifact not found at: {model_path}. Run train.py first."
            )
        artifact = joblib.load(model_path)
        self.model = artifact["model"]
        self.feature_columns = artifact["feature_columns"]
        self.target_column = artifact.get("target_column", "target_pm25_next_1h")
        self.method_used = "shap" if SHAP_AVAILABLE else "permutation_importance"
        self._explainer = None

    # ------------------------------------------------------------------
    # Global importance (model-level)
    # ------------------------------------------------------------------

    def global_importance(self, X: pd.DataFrame, y: pd.Series = None, n_repeats: int = 10, random_state: int = 42) -> list:
        """
        Returns ordered global feature contributions across a dataset.
        Uses SHAP mean(|shap_value|) if available, otherwise permutation
        importance (requires y).
        """
        X = X[self.feature_columns]

        if SHAP_AVAILABLE:
            return self._shap_global_importance(X)

        if y is None:
            raise ValueError(
                "Permutation importance fallback requires ground-truth `y`."
            )
        return self._permutation_global_importance(X, y, n_repeats, random_state)

    def _shap_global_importance(self, X: pd.DataFrame) -> list:
        explainer = self._get_shap_explainer()
        sample = X if len(X) <= 2000 else X.sample(2000, random_state=42)
        shap_values = explainer(sample)
        mean_abs = np.abs(shap_values.values).mean(axis=0)

        contributions = [
            {"feature": feat, "importance": float(val)}
            for feat, val in zip(self.feature_columns, mean_abs)
        ]
        contributions.sort(key=lambda x: x["importance"], reverse=True)
        return contributions

    def _permutation_global_importance(self, X: pd.DataFrame, y: pd.Series, n_repeats: int, random_state: int) -> list:
        result = permutation_importance(
            self.model,
            X,
            y,
            n_repeats=n_repeats,
            random_state=random_state,
            scoring="neg_mean_absolute_error",
        )
        contributions = [
            {
                "feature": feat,
                "importance": float(mean_val),
                "std": float(std_val),
            }
            for feat, mean_val, std_val in zip(
                self.feature_columns, result.importances_mean, result.importances_std
            )
        ]
        contributions.sort(key=lambda x: x["importance"], reverse=True)
        return contributions

    # ------------------------------------------------------------------
    # Local explanation (single prediction)
    # ------------------------------------------------------------------

    def explain_instance(self, record) -> dict:
        """
        Explain a single prediction. Returns ordered feature contributions
        for that specific record.
        """
        if isinstance(record, dict):
            df = pd.DataFrame([record])
        elif isinstance(record, pd.Series):
            df = record.to_frame().T
        else:
            df = record.copy()

        missing = [c for c in self.feature_columns if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required feature columns: {missing}")

        X = df[self.feature_columns].apply(pd.to_numeric, errors="coerce")
        X = X.fillna(X.median(numeric_only=True)).fillna(0)

        prediction = float(self.model.predict(X)[0])

        if SHAP_AVAILABLE:
            contributions = self._shap_local(X)
        else:
            contributions = self._permutation_local_proxy(X)

        return {
            "prediction": prediction,
            "method": self.method_used,
            "contributions": contributions,
        }

    def _shap_local(self, X: pd.DataFrame) -> list:
        explainer = self._get_shap_explainer()
        shap_values = explainer(X)
        values = shap_values.values[0]
        base_value = float(np.array(shap_values.base_values[0]).squeeze())

        contributions = [
            {
                "feature": feat,
                "value": float(X.iloc[0][feat]),
                "contribution": float(val),
            }
            for feat, val in zip(self.feature_columns, values)
        ]
        contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)
        return contributions

    def _permutation_local_proxy(self, X: pd.DataFrame) -> list:
        """
        Fallback local explanation when SHAP is unavailable: perturbs each
        feature to the training median (approximated via zero-out) one at a
        time and measures the resulting change in prediction. This gives a
        directionally meaningful, per-instance contribution estimate without
        requiring SHAP.
        """
        baseline_pred = float(self.model.predict(X)[0])
        contributions = []

        for feat in self.feature_columns:
            X_perturbed = X.copy()
            X_perturbed[feat] = 0.0
            perturbed_pred = float(self.model.predict(X_perturbed)[0])
            delta = baseline_pred - perturbed_pred

            contributions.append(
                {
                    "feature": feat,
                    "value": float(X.iloc[0][feat]),
                    "contribution": float(delta),
                }
            )

        contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)
        return contributions

    def _get_shap_explainer(self):
        if self._explainer is None:
            self._explainer = shap.Explainer(self.model)
        return self._explainer


def get_feature_attribution(record, model_path: str = DEFAULT_MODEL_PATH) -> dict:
    """Convenience function for explaining a single prediction."""
    attributor = FeatureAttributor(model_path=model_path)
    return attributor.explain_instance(record)


def get_global_feature_importance(
    features_csv_path: str,
    model_path: str = DEFAULT_MODEL_PATH,
    target_column: str = "target_pm25_next_1h",
) -> list:
    """Convenience function for global feature importance over a dataset."""
    attributor = FeatureAttributor(model_path=model_path)
    df = pd.read_csv(features_csv_path)
    y = df[target_column] if target_column in df.columns else None
    X = df[attributor.feature_columns]
    return attributor.global_importance(X, y)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="AIRIS feature attribution")
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--features", required=True, help="Path to features.csv")
    parser.add_argument(
        "--mode",
        choices=["global", "instance"],
        default="global",
    )
    parser.add_argument(
        "--row-index",
        type=int,
        default=-1,
        help="Row index to explain when mode=instance (default: last row)",
    )
    args = parser.parse_args()

    print(f"[attribution] SHAP available: {SHAP_AVAILABLE}")

    if args.mode == "global":
        result = get_global_feature_importance(args.features, model_path=args.model)
        print(json.dumps(result, indent=2))
    else:
        df = pd.read_csv(args.features)
        row = df.iloc[args.row_index]
        result = get_feature_attribution(row, model_path=args.model)
        print(json.dumps(result, indent=2))
