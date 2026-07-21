"""
AIRIS - Forecast Model Training
Loads features.csv, performs a chronological train/test split, trains a
HistGradientBoostingRegressor to predict target_pm25_next_1h, evaluates it,
and persists the model + metadata with joblib.
"""

import os
import json
import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import joblib

from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


DEFAULT_FEATURES_PATH = "data_pipeline/data/features/features.csv"
DEFAULT_ARTIFACT_DIR = "backend/ml/artifacts"
TARGET_COL = "target_pm25_next_1h"

NON_FEATURE_COLS = {
    "timestamp",
    TARGET_COL,
}


def load_features(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"features.csv not found at: {path}")
    df = pd.read_csv(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
    if TARGET_COL not in df.columns:
        raise ValueError(f"Target column '{TARGET_COL}' not found in features.csv")
    return df


def get_feature_columns(df: pd.DataFrame):
    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    # Keep only numeric columns; drop anything non-numeric / non-encodable
    numeric_cols = [c for c in feature_cols if pd.api.types.is_numeric_dtype(df[c])]
    dropped = set(feature_cols) - set(numeric_cols)
    if dropped:
        print(f"[train] Dropping non-numeric columns from feature set: {sorted(dropped)}")
    return numeric_cols


def chronological_split(df: pd.DataFrame, test_size: float = 0.2):
    n = len(df)
    split_idx = int(n * (1 - test_size))
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()
    return train_df, test_df


def train_model(
    features_path: str = DEFAULT_FEATURES_PATH,
    artifact_dir: str = DEFAULT_ARTIFACT_DIR,
    test_size: float = 0.2,
    random_state: int = 42,
):
    df = load_features(features_path)
    feature_cols = get_feature_columns(df)

    if len(feature_cols) == 0:
        raise ValueError("No usable numeric feature columns found.")

    train_df, test_df = chronological_split(df, test_size=test_size)

    X_train, y_train = train_df[feature_cols], train_df[TARGET_COL]
    X_test, y_test = test_df[feature_cols], test_df[TARGET_COL]

    print(f"[train] Train size: {len(X_train)}  Test size: {len(X_test)}  Features: {len(feature_cols)}")

    model = HistGradientBoostingRegressor(
        max_iter=300,
        learning_rate=0.05,
        max_depth=8,
        l2_regularization=0.1,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=random_state,
    )

    model.fit(X_train, y_train)

    preds = model.predict(X_test)

    mae = mean_absolute_error(y_test, preds)
    rmse = np.sqrt(mean_squared_error(y_test, preds))
    r2 = r2_score(y_test, preds)

    metrics = {
        "MAE": float(mae),
        "RMSE": float(rmse),
        "R2": float(r2),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "n_features": int(len(feature_cols)),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    print("[train] Evaluation metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    os.makedirs(artifact_dir, exist_ok=True)
    model_path = os.path.join(artifact_dir, "pm25_forecast_model.joblib")
    meta_path = os.path.join(artifact_dir, "pm25_forecast_metadata.json")

    joblib.dump(
        {
            "model": model,
            "feature_columns": feature_cols,
            "target_column": TARGET_COL,
        },
        model_path,
    )

    with open(meta_path, "w") as f:
        json.dump(
            {
                "metrics": metrics,
                "feature_columns": feature_cols,
                "target_column": TARGET_COL,
                "model_type": "HistGradientBoostingRegressor",
            },
            f,
            indent=2,
        )

    print(f"[train] Model saved -> {model_path}")
    print(f"[train] Metadata saved -> {meta_path}")

    return model, metrics


def parse_args():
    parser = argparse.ArgumentParser(description="Train AIRIS PM2.5 forecast model")
    parser.add_argument("--features", default=DEFAULT_FEATURES_PATH)
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--test-size", type=float, default=0.2)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    train_model(
        features_path=args.features,
        artifact_dir=args.artifact_dir,
        test_size=args.test_size,
    )
