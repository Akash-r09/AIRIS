"""
AIRIS - Feature Engineering
Reads the merged dataset (pollution + weather + fire + OSM) and produces
a fully engineered feature table (features.csv) ready for model training.
"""

import os
import argparse
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "data_pipeline/data/merged/merged_dataset.csv"
DEFAULT_OUTPUT = "data_pipeline/data/features/features.csv"

TIMESTAMP_CANDIDATES = [
    "timestamp_utc",
    "timestamp",
    "datetime",
    "datetime_utc",
    "date",
    "time",
    "ds",
]
PM25_CANDIDATES = [
    "aqi_pm25",
    "pm2_5",
    "pm25",
    "pm2.5",
    "PM2_5",
    "PM25",
]

LAG_HOURS = [1, 2, 3, 6, 12, 24]
ROLLING_WINDOWS = [3, 6, 12, 24]

WEATHER_KEYWORDS = [
    "temperature", "temp", "humidity", "wind_speed", "windspeed",
    "wind_direction", "winddirection", "pressure", "precipitation",
    "rain", "cloud", "cloudcover", "dew_point", "dewpoint", "visibility",
]

FIRE_KEYWORDS = [
    "fire", "frp", "hotspot", "brightness", "confidence_fire", "fire_count",
    "fire_distance", "nearest_fire",
]

OSM_KEYWORDS = [
    "osm", "road_density", "industrial", "landuse", "poi", "building",
    "population_density", "green_space", "distance_to_road", "traffic",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_column(df: pd.DataFrame, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _find_columns_by_keyword(df: pd.DataFrame, keywords):
    found = []
    for col in df.columns:
        col_lower = col.lower()
        for kw in keywords:
            if kw in col_lower:
                found.append(col)
                break
    return found


def load_merged_dataset(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Merged dataset not found at: {path}")
    df = pd.read_csv(path)

    ts_col = _find_column(df, TIMESTAMP_CANDIDATES)
    if ts_col is None:
        raise ValueError(
            "Could not locate a timestamp column in merged dataset. "
            f"Looked for: {TIMESTAMP_CANDIDATES}"
        )
    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce", utc=True)
    df = df.dropna(subset=[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)
    df = df.rename(columns={ts_col: "timestamp"})
    return df


# ---------------------------------------------------------------------------
# Feature blocks
# ---------------------------------------------------------------------------

def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    df["hour"] = df["timestamp"].dt.hour
    df["weekday"] = df["timestamp"].dt.weekday
    df["month"] = df["timestamp"].dt.month
    df["weekend"] = (df["weekday"] >= 5).astype(int)

    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24.0)
    df["weekday_sin"] = np.sin(2 * np.pi * df["weekday"] / 7.0)
    df["weekday_cos"] = np.cos(2 * np.pi * df["weekday"] / 7.0)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12.0)
    return df


def add_pm25_lag_and_rolling_features(df: pd.DataFrame, pm25_col: str) -> pd.DataFrame:
    for lag in LAG_HOURS:
        df[f"pm25_lag_{lag}h"] = df[pm25_col].shift(lag)

    for window in ROLLING_WINDOWS:
        df[f"pm25_roll_mean_{window}h"] = (
            df[pm25_col].shift(1).rolling(window=window, min_periods=1).mean()
        )
        df[f"pm25_roll_std_{window}h"] = (
            df[pm25_col].shift(1).rolling(window=window, min_periods=1).std()
        )
        df[f"pm25_roll_max_{window}h"] = (
            df[pm25_col].shift(1).rolling(window=window, min_periods=1).max()
        )

    df["pm25_diff_1h"] = df[pm25_col].diff(1)
    df["pm25_diff_3h"] = df[pm25_col].diff(3)
    return df


def add_weather_features(df: pd.DataFrame) -> pd.DataFrame:
    weather_cols = _find_columns_by_keyword(df, WEATHER_KEYWORDS)
    for col in weather_cols:
        if pd.api.types.is_numeric_dtype(df[col]):
            df[f"{col}_roll_mean_3h"] = df[col].shift(1).rolling(3, min_periods=1).mean()
    return df


def add_fire_features(df: pd.DataFrame) -> pd.DataFrame:
    fire_cols = _find_columns_by_keyword(df, FIRE_KEYWORDS)
    for col in fire_cols:
        if pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(0)
            df[f"{col}_roll_sum_24h"] = df[col].shift(1).rolling(24, min_periods=1).sum()
    if fire_cols:
        numeric_fire = [c for c in fire_cols if pd.api.types.is_numeric_dtype(df[c])]
        if numeric_fire:
            df["fire_activity_flag"] = (df[numeric_fire].sum(axis=1) > 0).astype(int)
    return df


def add_osm_features(df: pd.DataFrame) -> pd.DataFrame:
    osm_cols = _find_columns_by_keyword(df, OSM_KEYWORDS)
    for col in osm_cols:
        if pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(df[col].median())
    return df


def add_target(df: pd.DataFrame, pm25_col: str) -> pd.DataFrame:
    df["target_pm25_next_1h"] = df[pm25_col].shift(-1)
    return df


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def build_features(input_path: str, output_path: str) -> pd.DataFrame:
    df = load_merged_dataset(input_path)

    pm25_col = _find_column(df, PM25_CANDIDATES)
    if pm25_col is None:
        raise ValueError(
            f"Could not locate a PM2.5 column. Looked for: {PM25_CANDIDATES}"
        )

    df = add_time_features(df)
    df = add_pm25_lag_and_rolling_features(df, pm25_col)
    df = add_weather_features(df)
    df = add_fire_features(df)
    df = add_osm_features(df)
    df = add_target(df, pm25_col)

    if pm25_col != "pm2_5":
        df = df.rename(columns={pm25_col: "pm2_5"})

    # Drop rows without enough history for lag features or without a target
    df = df.dropna(subset=["target_pm25_next_1h"])
    lag_cols = [f"pm25_lag_{lag}h" for lag in LAG_HOURS]
    df = df.dropna(subset=lag_cols)

    # Fill any remaining numeric NaNs (weather/OSM edge cases) with column median
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    df[numeric_cols] = df[numeric_cols].fillna(df[numeric_cols].median())

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"[feature_engineering] Saved {len(df)} rows x {len(df.columns)} cols -> {output_path}")
    return df


def parse_args():
    parser = argparse.ArgumentParser(description="AIRIS feature engineering")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to merged dataset CSV")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Path to save features.csv")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_features(args.input, args.output)
