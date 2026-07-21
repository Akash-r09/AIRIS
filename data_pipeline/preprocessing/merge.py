#!/usr/bin/env python3
"""
data_pipeline/preprocessing/merge.py

Orchestrates the preprocessing stage: reads every ingestion module's raw
output for a city (via clean.py), merges them into a single chronological
dataset, and writes data_pipeline/processed/<city>/clean_dataset.csv plus
a manifest.json. Pure cleaning/validation/merging/alignment — no feature
engineering, no ML, no forecasting.

Merge strategy:
  - AQI (from openaq.py) is the spine: every other source is attached onto
    AQI's own timestamps, since AQI is the target variable and "one row
    per timestamp" means one row per AQI observation. AQI is the only
    source treated as required — if it's missing or empty, the run fails
    outright (never fabricate a dataset without real AQI values).
  - Weather is merged onto the AQI spine by *nearest* timestamp (pandas
    merge_asof, direction="nearest"), with a tolerance so a weather series
    that's offset from AQI by more than an hour doesn't get matched to a
    misleadingly distant reading.
  - Fire counts are merged by *exact* hour match (a left join on the
    floored-to-hour timestamp), not nearest — an hourly count is either
    for that hour or it isn't, so "nearest" doesn't apply the way it does
    to a continuous weather measurement. Hours with no fire file, or no
    detections in that hour, get fire_count=0 (see clean.py's docstring
    for why this is not "fabricating AQI").
  - OSM is static (no timestamp dimension at all) and is broadcast onto
    every row as constant columns.

Usage:
    python data_pipeline/preprocessing/merge.py \\
        --city Delhi \\
        --input-root data_pipeline/raw \\
        --output-root data_pipeline/processed

Exit codes:
    0  success or partial_success — a dataset with at least one row was written
    1  failure — AQI data missing/empty, or no usable rows could be produced
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from . import clean, validators
except ImportError:  # allows running this file directly as well as importing it as a package
    import clean
    import validators

logger = logging.getLogger("preprocessing")

# Weather timestamps within this window of an AQI timestamp are considered
# "the same hour" for merge_asof's nearest-match — wide enough to tolerate
# minor timestamp misalignment between sources, narrow enough that a
# multi-hour data gap in one source doesn't get silently paired with a
# distant reading from the other.
WEATHER_MERGE_TOLERANCE = pd.Timedelta("30min")


def resolve_city_dirs(input_root: Path, city_slug: str) -> dict[str, Path]:
    return {
        "openaq": input_root / "openaq" / city_slug,
        "openmeteo": input_root / "openmeteo" / city_slug,
        "firms": input_root / "firms" / city_slug,
        "osm": input_root / "osm" / city_slug,
    }


def merge_weather_onto_aqi(aqi_df: pd.DataFrame, weather_df: pd.DataFrame | None) -> tuple[pd.DataFrame, int]:
    """
    Merges weather onto the AQI spine by nearest timestamp. Returns
    (merged_df, missing_values_filled). Even when clean.py's own
    forward/backward fill has already closed gaps within weather's native
    timeline, merge_asof onto AQI's (differently-timed, possibly sparser)
    spine can still introduce new gaps -- e.g. an AQI timestamp with no
    weather reading inside the tolerance window. Those are filled here,
    after the join, for the same reason: only weather is ever filled,
    never AQI.
    """
    if weather_df is None or weather_df.empty:
        logger.warning("No weather data available -- proceeding without weather columns.")
        return aqi_df, 0

    left = aqi_df.sort_values("timestamp_utc")
    right = weather_df.sort_values("timestamp_utc")

    merged = pd.merge_asof(
        left, right,
        on="timestamp_utc",
        direction="nearest",
        tolerance=WEATHER_MERGE_TOLERANCE,
    )

    weather_cols = [c for c in merged.columns if c.startswith(clean.WEATHER_VARIABLES_PREFIX)]
    n_missing_before = int(merged[weather_cols].isna().sum().sum())
    merged[weather_cols] = merged[weather_cols].ffill().bfill()
    n_missing_after = int(merged[weather_cols].isna().sum().sum())
    filled = n_missing_before - n_missing_after

    return merged, filled


def merge_fires_onto_aqi(df: pd.DataFrame, fires_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Merges hourly fire counts onto the dataset by exact hour match. Hours
    with no matching fire data get fire_count=0 -- a legitimate default
    for a count column, not a fabricated measurement (see clean.py).
    """
    merged = df.copy()
    merged["timestamp_hour"] = merged["timestamp_utc"].dt.floor("h")

    if fires_df is None or fires_df.empty:
        logger.warning("No fire detection data available -- fire_count set to 0 for all rows.")
        merged["fire_count"] = 0
        return merged.drop(columns=["timestamp_hour"])

    fires = fires_df.rename(columns={"timestamp_utc": "timestamp_hour"})
    merged = merged.merge(fires, on="timestamp_hour", how="left")
    merged["fire_count"] = merged["fire_count"].fillna(0).astype(int)
    return merged.drop(columns=["timestamp_hour"])


def attach_osm_columns(df: pd.DataFrame, osm_counts: dict[str, int]) -> pd.DataFrame:
    """Broadcasts the static OSM category counts as constant columns across every row."""
    result = df.copy()
    for column, value in osm_counts.items():
        result[column] = value
    return result


def build_manifest(
    city: str,
    city_dirs: dict[str, Path],
    stats_by_source: dict[str, clean.CleaningStats],
    total_duplicates_removed: int,
    output_rows: int,
    output_file: str | None,
    status: str,
    extra_missing_values_filled: int = 0,
) -> dict[str, Any]:
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "city": city,
        "input_files": {
            source: {
                "directory": str(city_dirs[source]),
                "found": stats.file_found,
                "rows_read": stats.rows_read,
            }
            for source, stats in stats_by_source.items()
        },
        "rows_read": sum(s.rows_read for s in stats_by_source.values()),
        "rows_removed": sum(s.rows_removed_malformed for s in stats_by_source.values()),
        "duplicates_removed": total_duplicates_removed,
        "missing_values_filled": sum(s.missing_values_filled for s in stats_by_source.values()) + extra_missing_values_filled,
        "output_rows": output_rows,
        "output_file": output_file,
        "status": status,
    }


def run_preprocessing(city: str, input_root: Path, output_root: Path) -> int:
    city_slug = validators.slugify_city(city)
    city_dirs = resolve_city_dirs(input_root, city_slug)
    output_dir = output_root / city_slug

    logger.info("Starting preprocessing for city=%s (slug=%s)", city, city_slug)

    aqi_df, aqi_stats = clean.load_and_clean_openaq(city_dirs["openaq"])
    weather_df, weather_stats = clean.load_and_clean_openmeteo(city_dirs["openmeteo"])
    fires_df, fires_stats = clean.load_and_clean_firms(city_dirs["firms"])
    osm_counts, osm_stats = clean.load_and_clean_osm(city_dirs["osm"])

    stats_by_source = {
        "openaq": aqi_stats,
        "openmeteo": weather_stats,
        "firms": fires_stats,
        "osm": osm_stats,
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    if aqi_df is None or aqi_df.empty:
        logger.error(
            "No usable AQI data for city=%s -- refusing to produce a dataset without real AQI values.",
            city,
        )
        manifest = build_manifest(
            city, city_dirs, stats_by_source,
            total_duplicates_removed=sum(s.duplicates_removed for s in stats_by_source.values()),
            output_rows=0, output_file=None, status="failed_zero_rows",
        )
        write_manifest(output_dir, manifest)
        return 1

    merged, post_merge_filled = merge_weather_onto_aqi(aqi_df, weather_df)
    merged = merge_fires_onto_aqi(merged, fires_df)
    merged = attach_osm_columns(merged, osm_counts)

    merged = merged.sort_values("timestamp_utc")
    before_final_dedup = len(merged)
    merged = merged.drop_duplicates(subset=["timestamp_utc"], keep="first")
    final_dupes_removed = before_final_dedup - len(merged)

    merged["timestamp_utc"] = merged["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if merged.empty:
        logger.error("Merged dataset for city=%s is empty after final alignment.", city)
        manifest = build_manifest(
            city, city_dirs, stats_by_source,
            total_duplicates_removed=sum(s.duplicates_removed for s in stats_by_source.values()) + final_dupes_removed,
            output_rows=0, output_file=None, status="failed_zero_rows",
            extra_missing_values_filled=post_merge_filled,
        )
        write_manifest(output_dir, manifest)
        return 1

    output_path = output_dir / "clean_dataset.csv"
    merged.to_csv(output_path, index=False)

    total_duplicates_removed = sum(s.duplicates_removed for s in stats_by_source.values()) + final_dupes_removed
    degraded = not (weather_stats.file_found and fires_stats.file_found and osm_stats.file_found)
    status = "partial_success" if degraded else "success"

    manifest = build_manifest(
        city, city_dirs, stats_by_source,
        total_duplicates_removed=total_duplicates_removed,
        output_rows=len(merged), output_file=str(output_path), status=status,
        extra_missing_values_filled=post_merge_filled,
    )
    write_manifest(output_dir, manifest)

    logger.info(
        "Preprocessing complete for city=%s: %d output rows, status=%s, output=%s",
        city, len(merged), status, output_path,
    )
    return 0


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> Path:
    manifest_path = output_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    logger.info("Wrote manifest -> %s", manifest_path)
    return manifest_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean and merge all ingestion outputs for a city into one ML-ready dataset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--city", required=True, help="City name (e.g. 'Delhi'). Used to locate input directories and slug the output path.")
    parser.add_argument("--input-root", type=Path, default=Path("data_pipeline/raw"), help="Root directory containing openaq/openmeteo/firms/osm subfolders.")
    parser.add_argument("--output-root", type=Path, default=Path("data_pipeline/processed"), help="Root directory to write <city>/clean_dataset.csv and manifest.json into.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    return run_preprocessing(args.city, args.input_root, args.output_root)


if __name__ == "__main__":
    sys.exit(main())
