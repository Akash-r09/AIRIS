#!/usr/bin/env python3
"""
data_pipeline/preprocessing/validators.py

Shared validation primitives for the preprocessing stage: the exception
type raised across merge.py/clean.py, the required-column schema for each
ingestion source, and helpers for normalizing/validating timestamps and
dataframe shapes. This module has no dependency on clean.py or merge.py —
it is the leaf of the preprocessing package's internal dependency graph,
mirroring the "validators are pure and dependency-free" pattern implied by
the other ingestion modules' own inline validation functions.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("preprocessing")


class PreprocessingError(Exception):
    """Raised for any unrecoverable failure while cleaning or merging a
    single city's data. Caught at the source-file level in clean.py so one
    malformed file doesn't necessarily abort the whole run — never caught
    silently."""


# Minimum columns each ingestion source's CSV must have for its data to be
# usable at all. These are intentionally narrow: only what cleaning/merging
# actually depends on, not every column each ingest module happens to
# write (e.g. openaq.py's location_name or firms.py's confidence are
# useful metadata but not required for building the ML dataset).
OPENAQ_REQUIRED_COLUMNS = {"timestamp_utc", "parameter", "value"}
OPENMETEO_REQUIRED_COLUMNS = {"timestamp_utc", "variable", "value"}
FIRMS_REQUIRED_COLUMNS = {"timestamp_utc"}
OSM_REQUIRED_COLUMNS = {"feature_category"}


def validate_columns(df: pd.DataFrame, required: set[str], source_name: str, file_path: Path) -> None:
    """
    Raises PreprocessingError if any required column is missing. Never
    silently proceeds with a malformed schema — the caller decides whether
    that failure is fatal to the whole run or just to this one file.
    """
    missing = required - set(df.columns)
    if missing:
        raise PreprocessingError(
            f"{source_name} file {file_path} is missing required columns {sorted(missing)}. "
            f"Columns present: {sorted(df.columns)}."
        )


def normalize_timestamps(df: pd.DataFrame, column: str, source_name: str) -> tuple[pd.DataFrame, int]:
    """
    Parses `column` to UTC-aware pandas Timestamps in place, dropping rows
    where parsing fails. Returns (df_with_valid_timestamps, rows_dropped).
    Never silently keeps a row with an unparseable timestamp — every drop
    is logged.
    """
    if df.empty:
        return df, 0

    parsed = pd.to_datetime(df[column], utc=True, errors="coerce")
    invalid_mask = parsed.isna()
    n_invalid = int(invalid_mask.sum())

    if n_invalid:
        bad_examples = df.loc[invalid_mask, column].astype(str).unique()[:5]
        logger.warning(
            "%s: dropping %d row(s) with unparseable %s values (examples: %s).",
            source_name, n_invalid, column, list(bad_examples),
        )

    result = df.loc[~invalid_mask].copy()
    result[column] = parsed.loc[~invalid_mask]
    return result, n_invalid


def validate_numeric_column(df: pd.DataFrame, column: str, source_name: str) -> tuple[pd.DataFrame, int]:
    """
    Coerces `column` to numeric, dropping rows where it fails. Returns
    (df, rows_dropped). Used for value columns where a non-numeric entry
    indicates a genuinely malformed record, not a legitimate category.
    """
    if df.empty:
        return df, 0

    coerced = pd.to_numeric(df[column], errors="coerce")
    invalid_mask = coerced.isna()
    n_invalid = int(invalid_mask.sum())

    if n_invalid:
        logger.warning("%s: dropping %d row(s) with non-numeric or missing %s.", source_name, n_invalid, column)

    result = df.loc[~invalid_mask].copy()
    result[column] = coerced.loc[~invalid_mask]
    return result, n_invalid


def drop_duplicate_rows(df: pd.DataFrame, subset: list[str], source_name: str) -> tuple[pd.DataFrame, int]:
    """Drops exact duplicate rows on `subset`, keeping the first occurrence. Returns (df, n_removed)."""
    if df.empty:
        return df, 0

    before = len(df)
    result = df.drop_duplicates(subset=subset, keep="first").copy()
    n_removed = before - len(result)

    if n_removed:
        logger.info("%s: removed %d duplicate row(s) on %s.", source_name, n_removed, subset)

    return result, n_removed


def slugify_city(city: str) -> str:
    """
    Matches the city-folder slugification already used by every ingestion
    module (lowercase, non-alphanumeric -> underscore) — duplicated here
    rather than imported, consistent with each ingestion module's own
    precedent of duplicating small shared constants/helpers for
    independence rather than cross-importing sibling modules.
    """
    return "".join(c if c.isalnum() else "_" for c in city.strip().lower())
