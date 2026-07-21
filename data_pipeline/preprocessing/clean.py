#!/usr/bin/env python3
"""
data_pipeline/preprocessing/clean.py

Per-source cleaning: reads each ingestion module's raw CSV output for a
city, validates it, normalizes timestamps to UTC, removes duplicates, and
reshapes it into the form merge.py needs. No feature engineering happens
here — pivoting long-format (timestamp, parameter, value) rows into wide
columns and averaging across monitoring stations is normalization, not
derived signal creation; nothing here computes a value that wasn't already
directly reported by an ingestion source.

Each load_and_clean_* function returns (dataframe_or_None, CleaningStats).
A None dataframe means the source's raw files were missing or contributed
zero usable rows after cleaning — callers decide whether that's fatal
(AQI) or degrades gracefully (weather, fire, OSM).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

try:
    from . import validators
except ImportError:  # allows running this file directly as well as importing it as a package
    import validators

logger = logging.getLogger("preprocessing")

# Fixed OSM category list, matching the categories osm.py's FEATURE_SPECS
# actually produces plus its "other" fallback. Kept fixed (rather than
# derived per-city from whatever categories happen to appear) so every
# city's clean_dataset.csv has the same OSM columns regardless of what was
# found — consistent schema matters more here than being minimal.
OSM_CATEGORIES = ["roads", "industrial", "transport", "green_space", "water", "other"]

WEATHER_VARIABLES_PREFIX = "weather_"
AQI_PARAMETER_PREFIX = "aqi_"


@dataclass
class CleaningStats:
    source: str
    file_found: bool
    rows_read: int = 0
    rows_removed_malformed: int = 0
    duplicates_removed: int = 0
    missing_values_filled: int = 0
    output_rows: int = 0


# --------------------------------------------------------------------------
# OpenAQ (AQI)
# --------------------------------------------------------------------------

def load_and_clean_openaq(city_dir: Path) -> tuple[pd.DataFrame | None, CleaningStats]:
    """
    Reads every location_*.csv in city_dir (openaq.py writes one file per
    monitoring station), concatenates them, cleans, and aggregates to a
    single city-level time series by averaging across stations at each
    (timestamp, parameter) — then pivots parameter into wide columns
    prefixed "aqi_" (e.g. aqi_pm25, aqi_pm10).
    """
    stats = CleaningStats(source="openaq", file_found=False)

    if not city_dir.is_dir():
        logger.warning("OpenAQ directory not found: %s", city_dir)
        return None, stats

    files = sorted(city_dir.glob("location_*.csv"))
    if not files:
        logger.warning("No OpenAQ location CSVs found in %s", city_dir)
        return None, stats

    stats.file_found = True
    frames = []
    for file_path in files:
        try:
            df = pd.read_csv(file_path)
        except (pd.errors.ParserError, OSError) as exc:
            logger.warning("Skipping unreadable OpenAQ file %s: %s", file_path, exc)
            continue

        try:
            validators.validate_columns(df, validators.OPENAQ_REQUIRED_COLUMNS, "OpenAQ", file_path)
        except validators.PreprocessingError as exc:
            logger.warning("Skipping malformed OpenAQ file: %s", exc)
            continue

        stats.rows_read += len(df)
        frames.append(df)

    if not frames:
        logger.warning("No usable OpenAQ files after validation in %s", city_dir)
        return None, stats

    combined = pd.concat(frames, ignore_index=True)

    combined, n_bad_ts = validators.normalize_timestamps(combined, "timestamp_utc", "OpenAQ")
    stats.rows_removed_malformed += n_bad_ts

    combined, n_bad_value = validators.validate_numeric_column(combined, "value", "OpenAQ")
    stats.rows_removed_malformed += n_bad_value

    combined, n_dupes = validators.drop_duplicate_rows(
        combined, subset=["timestamp_utc", "location_id", "parameter"], source_name="OpenAQ"
    )
    stats.duplicates_removed += n_dupes

    if combined.empty:
        logger.warning("OpenAQ data for %s was empty after cleaning.", city_dir)
        return None, stats

    # Average across stations at each (timestamp, parameter) -- a city-level
    # aggregate, not a derived feature: it's still directly the reported
    # pollutant concentration, just averaged spatially across stations.
    aggregated = (
        combined.groupby(["timestamp_utc", "parameter"], as_index=False)["value"].mean()
    )
    wide = aggregated.pivot(index="timestamp_utc", columns="parameter", values="value")
    wide.columns = [f"{AQI_PARAMETER_PREFIX}{c}" for c in wide.columns]
    wide = wide.reset_index().sort_values("timestamp_utc")

    stats.output_rows = len(wide)
    return wide, stats


# --------------------------------------------------------------------------
# Open-Meteo (weather)
# --------------------------------------------------------------------------

def load_and_clean_openmeteo(city_dir: Path) -> tuple[pd.DataFrame | None, CleaningStats]:
    """
    Reads the single *_weather.csv openmeteo.py writes per city, cleans,
    pivots variable into wide columns prefixed "weather_", and forward/
    backward-fills any remaining gaps in those weather columns only --
    AQI values are never filled this way (see module docstring).
    """
    stats = CleaningStats(source="openmeteo", file_found=False)

    if not city_dir.is_dir():
        logger.warning("Open-Meteo directory not found: %s", city_dir)
        return None, stats

    files = sorted(city_dir.glob("*_weather.csv"))
    if not files:
        logger.warning("No Open-Meteo weather CSV found in %s", city_dir)
        return None, stats

    file_path = files[0]
    try:
        df = pd.read_csv(file_path)
    except (pd.errors.ParserError, OSError) as exc:
        logger.warning("Unreadable Open-Meteo file %s: %s", file_path, exc)
        return None, stats

    try:
        validators.validate_columns(df, validators.OPENMETEO_REQUIRED_COLUMNS, "Open-Meteo", file_path)
    except validators.PreprocessingError as exc:
        logger.warning("Skipping malformed Open-Meteo file: %s", exc)
        return None, stats

    stats.file_found = True
    stats.rows_read = len(df)

    df, n_bad_ts = validators.normalize_timestamps(df, "timestamp_utc", "Open-Meteo")
    stats.rows_removed_malformed += n_bad_ts

    df, n_bad_value = validators.validate_numeric_column(df, "value", "Open-Meteo")
    stats.rows_removed_malformed += n_bad_value

    df, n_dupes = validators.drop_duplicate_rows(
        df, subset=["timestamp_utc", "variable"], source_name="Open-Meteo"
    )
    stats.duplicates_removed += n_dupes

    if df.empty:
        logger.warning("Open-Meteo data for %s was empty after cleaning.", city_dir)
        return None, stats

    wide = df.pivot(index="timestamp_utc", columns="variable", values="value")
    wide.columns = [f"{WEATHER_VARIABLES_PREFIX}{c}" for c in wide.columns]
    wide = wide.reset_index().sort_values("timestamp_utc")

    weather_cols = [c for c in wide.columns if c.startswith(WEATHER_VARIABLES_PREFIX)]
    n_missing_before = int(wide[weather_cols].isna().sum().sum())
    wide[weather_cols] = wide[weather_cols].ffill().bfill()
    n_missing_after = int(wide[weather_cols].isna().sum().sum())
    stats.missing_values_filled = n_missing_before - n_missing_after

    stats.output_rows = len(wide)
    return wide, stats


# --------------------------------------------------------------------------
# NASA FIRMS (fire detections)
# --------------------------------------------------------------------------

def load_and_clean_firms(city_dir: Path) -> tuple[pd.DataFrame | None, CleaningStats]:
    """
    Reads fires.csv (if present), normalizes timestamps, and aggregates
    detections into hourly counts: one row per hour with a fire_count
    column. Filling absent hours with a count of 0 later (in merge.py) is
    not "fabricating AQI" -- zero is the factually correct default for a
    count of detections, unlike a pollutant concentration that was never
    measured.
    """
    stats = CleaningStats(source="firms", file_found=False)

    if not city_dir.is_dir():
        logger.warning("FIRMS directory not found: %s", city_dir)
        return None, stats

    file_path = city_dir / "fires.csv"
    if not file_path.is_file():
        logger.warning("No FIRMS fires.csv found in %s", city_dir)
        return None, stats

    try:
        df = pd.read_csv(file_path)
    except (pd.errors.ParserError, OSError) as exc:
        logger.warning("Unreadable FIRMS file %s: %s", file_path, exc)
        return None, stats

    try:
        validators.validate_columns(df, validators.FIRMS_REQUIRED_COLUMNS, "FIRMS", file_path)
    except validators.PreprocessingError as exc:
        logger.warning("Skipping malformed FIRMS file: %s", exc)
        return None, stats

    stats.file_found = True
    stats.rows_read = len(df)

    if df.empty:
        logger.info("FIRMS file for %s has zero detections (valid empty result).", city_dir)
        return None, stats

    df, n_bad_ts = validators.normalize_timestamps(df, "timestamp_utc", "FIRMS")
    stats.rows_removed_malformed += n_bad_ts

    if df.empty:
        logger.warning("FIRMS data for %s was empty after timestamp cleaning.", city_dir)
        return None, stats

    df["timestamp_utc"] = df["timestamp_utc"].dt.floor("h")
    hourly_counts = df.groupby("timestamp_utc", as_index=False).size().rename(columns={"size": "fire_count"})

    stats.output_rows = len(hourly_counts)
    return hourly_counts, stats


# --------------------------------------------------------------------------
# OSM (static features)
# --------------------------------------------------------------------------

def load_and_clean_osm(city_dir: Path) -> tuple[dict[str, int], CleaningStats]:
    """
    Reads osm_features.csv (if present) and counts features per category.
    Returns a dict {osm_<category>_count: count} covering every entry in
    OSM_CATEGORIES (0 for any category not present), plus stats. OSM data
    is static -- there is no timestamp dimension here at all; merge.py
    broadcasts this dict as constant columns across every row.
    """
    stats = CleaningStats(source="osm", file_found=False)
    zero_counts = {f"osm_{cat}_count": 0 for cat in OSM_CATEGORIES}

    if not city_dir.is_dir():
        logger.warning("OSM directory not found: %s", city_dir)
        return zero_counts, stats

    file_path = city_dir / "osm_features.csv"
    if not file_path.is_file():
        logger.warning("No OSM osm_features.csv found in %s", city_dir)
        return zero_counts, stats

    try:
        df = pd.read_csv(file_path)
    except (pd.errors.ParserError, OSError) as exc:
        logger.warning("Unreadable OSM file %s: %s", file_path, exc)
        return zero_counts, stats

    try:
        validators.validate_columns(df, validators.OSM_REQUIRED_COLUMNS, "OSM", file_path)
    except validators.PreprocessingError as exc:
        logger.warning("Skipping malformed OSM file: %s", exc)
        return zero_counts, stats

    stats.file_found = True
    stats.rows_read = len(df)

    if df.empty:
        logger.info("OSM file for %s has zero features.", city_dir)
        return zero_counts, stats

    counts_by_category = df["feature_category"].value_counts().to_dict()
    result = dict(zero_counts)
    for category, count in counts_by_category.items():
        key = f"osm_{category}_count"
        result[key] = int(count)  # any category outside OSM_CATEGORIES (e.g. "other") is added as-is

    stats.output_rows = len(result)
    return result, stats
