#!/usr/bin/env python3
"""
data_pipeline/ingest/firms.py

Downloads historical fire/thermal-anomaly detections from the NASA FIRMS
Area API (VIIRS/MODIS standard-quality products) for a target city's
upwind bounding box and date range, and writes one CSV into the output
directory. This data feeds the source-attribution engine's stubble-burning
signal, joined against the AQI and weather datasets from
data_pipeline/ingest/openaq.py and openmeteo.py in the feature engineering
stage (Milestone 3).

Design decisions below are frozen per project instructions and are not
re-derived here — see docs/architecture.md for the research that produced
them.

NASA FIRMS Area API background:
  - Endpoint: https://firms.modaps.eosdis.nasa.gov/api/area/csv/
  - URL shape: .../csv/[MAP_KEY]/[SOURCE]/[AREA_COORDINATES]/[DAY_RANGE]/[DATE]
    returns data for [DATE .. DATE + DAY_RANGE - 1].
  - Authentication: a free MAP_KEY, embedded directly in the URL path (not
    a header). Read from the NASA_FIRMS_MAP_KEY environment variable via
    os.getenv() — never hardcoded, never CLI-supplied. Because the key is
    part of the URL itself, every log message that includes a request URL
    is redacted before logging so the key never lands in plaintext logs.
  - DAY_RANGE is capped at 5 by NASA — longer ranges are split into 5-day
    chunks here, mirroring the chunking approach in openaq.py/openmeteo.py.
  - Historical (non-recent) requests must use "_SP" (Standard Processing /
    science-quality, reprocessed) sources rather than "_NRT" sources.
    Default source is VIIRS_NOAA20_SP; configurable via --source among the
    documented SP products.
  - The response is plain CSV, not JSON. Different sensors return
    different column sets (e.g. MODIS includes "brightness" and "type";
    VIIRS instead reports "bright_ti4"/"bright_ti5" and has no "type"
    column at all). Because of this real cross-sensor variability, this
    script does NOT assume a fixed schema: every column NASA returns is
    parsed and written through untouched via csv.DictReader/DictWriter,
    with only latitude/longitude/acq_date/acq_time treated as universally
    required. One derived column, timestamp_utc (combining acq_date +
    acq_time, which FIRMS documents as UTC), is appended for consistency
    with the timestamp_utc columns already produced by openaq.py and
    openmeteo.py — this adds a column, it never discards one.
  - A response can be HTTP 200 with a plain-text error body instead of CSV
    (e.g. for an invalid MAP_KEY) rather than a structured error object or
    non-200 status. This script treats any 200 response whose header row
    doesn't contain the required minimum columns as an API-level error,
    not as valid (if unexpected) data.

Usage:
    python data_pipeline/ingest/firms.py \\
        --city Delhi \\
        --start-date 2024-10-25 \\
        --end-date 2024-11-10 \\
        --output-dir data_pipeline/raw/firms

Exit codes:
    0  success — at least one row of fire detection data was collected
    1  failure — bad arguments, missing/invalid API key, API error after
       retries exhausted, or zero rows of data collected for the range
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

API_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Identical to CITY_BBOX_PRESETS in openaq.py / openmeteo.py — reused
# verbatim, not redefined, per the frozen city-support requirement. Stored
# as (min_lon, min_lat, max_lon, max_lat), which is already the same
# ordering FIRMS calls "west,south,east,north" — no conversion needed
# beyond formatting as a comma-joined string.
CITY_BBOX_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "delhi": (76.84, 28.40, 77.35, 28.88),
    "mumbai": (72.77, 18.89, 72.98, 19.27),
    "kolkata": (88.24, 22.45, 88.48, 22.66),
    "bengaluru": (77.46, 12.83, 77.75, 13.14),
    "bangalore": (77.46, 12.83, 77.75, 13.14),
    "chennai": (80.16, 12.90, 80.32, 13.23),
}

# Standard Processing (science-quality, reprocessed) sources only —
# historical requests must not use "_NRT" sources per the frozen design
# decision. This is the allowlist --source is validated against.
VALID_SP_SOURCES = ["MODIS_SP", "VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "VIIRS_NOAA21_SP"]
DEFAULT_SOURCE = "VIIRS_NOAA20_SP"

# NASA hard limit — not configurable.
DAY_RANGE = 5

# Every FIRMS sensor product includes these columns; everything else is
# preserved dynamically because it varies by sensor (see module docstring).
REQUIRED_COLUMNS = {"latitude", "longitude", "acq_date", "acq_time"}

# Fields validated as numeric when present, for anomaly flagging only —
# never used to drop a record (only invalid latitude/longitude do that).
# "confidence" is deliberately excluded: MODIS reports it as 0-100, VIIRS
# standard product reports it categorically ("l"/"n"/"h"), so it is never
# forced through numeric validation.
NUMERIC_SANITY_FIELDS: dict[str, tuple[float, float]] = {
    "latitude": (-90, 90),
    "longitude": (-180, 180),
    "brightness": (200, 500),
    "bright_ti4": (200, 500),
    "bright_ti5": (200, 500),
    "bright_t31": (200, 500),
    "frp": (0, 100000),
    "scan": (0, 10),
    "track": (0, 10),
}

REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
BACKOFF_FACTOR = 1.0

logger = logging.getLogger("firms_ingest")


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class FirmsIngestError(Exception):
    """Raised for any unrecoverable failure in a single chunk fetch/parse.
    Caught per-chunk in main() so one bad chunk doesn't abort the whole
    run — never caught silently."""


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class ChunkResult:
    rows: list[dict[str, str]]
    fieldnames: list[str]


@dataclass
class CitySummary:
    city: str
    source: str
    bbox: tuple[float, float, float, float]
    rows_written: int = 0
    rows_skipped: int = 0
    anomalies: int = 0
    chunks_fetched: int = 0
    chunks_failed: int = 0
    output_file: str | None = None
    columns_written: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# HTTP session with retry policy
# --------------------------------------------------------------------------

def build_session() -> requests.Session:
    """
    Builds a requests.Session with automatic retry (exponential backoff)
    for connection errors and transient HTTP statuses.
    """
    session = requests.Session()
    session.headers.update({
        "Accept": "text/csv",
        "User-Agent": "AIRIS-data-pipeline/0.1 (+hackathon prototype)",
    })

    retry = Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        status=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        allowed_methods=["GET"],
        raise_on_status=False,  # final response is inspected explicitly below
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _redact_map_key(url: str, map_key: str) -> str:
    """Never let the MAP_KEY reach a log line in plaintext."""
    if not map_key:
        return url
    return url.replace(map_key, "***REDACTED***")


def fetch_chunk_csv(session: requests.Session, url: str, map_key: str) -> str:
    """
    Issues a GET request to the Area API and returns the raw response text.
    Raises FirmsIngestError (with the MAP_KEY redacted from the message)
    on any failure that survives the retry policy.
    """
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        raise FirmsIngestError(
            f"Network error calling FIRMS API ({_redact_map_key(url, map_key)}): {exc}"
        ) from exc

    if response.status_code != 200:
        raise FirmsIngestError(
            f"FIRMS API returned HTTP {response.status_code} for {_redact_map_key(url, map_key)}. "
            f"Body: {response.text[:300]!r}"
        )

    return response.text


# --------------------------------------------------------------------------
# Date chunking
# --------------------------------------------------------------------------

def iter_date_chunks(start: datetime, end: datetime, chunk_days: int) -> Iterator[datetime]:
    """
    Yields chunk start dates covering [start, end], each chunk spanning at
    most chunk_days (NASA returns [DATE .. DATE + DAY_RANGE - 1] for a
    single request, so only the start date needs to be yielded).
    """
    cursor = start
    delta = timedelta(days=chunk_days - 1)
    while cursor <= end:
        yield cursor
        cursor = cursor + delta + timedelta(days=1)


# --------------------------------------------------------------------------
# Parsing & validation
# --------------------------------------------------------------------------

def parse_csv_text(text: str, city: str) -> ChunkResult:
    """
    Parses raw FIRMS CSV text into rows preserving every original column.
    Raises FirmsIngestError if the response doesn't look like valid FIRMS
    CSV (missing required columns) — this is what catches API-level error
    text (e.g. an invalid-key message) that NASA sometimes returns with an
    HTTP 200 status instead of a proper error code.
    """
    stripped = text.strip()
    if not stripped:
        raise FirmsIngestError(f"Empty response body from FIRMS API for {city}.")

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames

    if not fieldnames:
        raise FirmsIngestError(f"FIRMS response for {city} has no CSV header. Body: {stripped[:300]!r}")

    normalized_fieldnames = {f.strip().lower() for f in fieldnames}
    missing = REQUIRED_COLUMNS - normalized_fieldnames
    if missing:
        raise FirmsIngestError(
            f"FIRMS response for {city} is missing required columns {sorted(missing)}. "
            f"This usually indicates an API-level error (e.g. invalid MAP_KEY) rather than valid "
            f"fire data. Response header: {fieldnames!r}. Body preview: {stripped[:300]!r}"
        )

    rows = list(reader)
    return ChunkResult(rows=rows, fieldnames=list(fieldnames))


def validate_and_enrich_row(
    row: dict[str, str],
    city: str,
    row_index: int,
) -> tuple[dict[str, str], bool] | None:
    """
    Validates one FIRMS record. Returns (enriched_row, is_anomalous) or
    None if the record must be dropped (only for missing/invalid
    latitude/longitude — every other field is preserved as-is even if it
    fails a sanity check, per "never discard columns" / "never silently
    discard records": anomalies are flagged, not removed).
    """
    lat_raw = row.get("latitude")
    lon_raw = row.get("longitude")

    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        logger.warning(
            "Dropping record %d for %s: latitude/longitude not numeric (lat=%r, lon=%r).",
            row_index, city, lat_raw, lon_raw,
        )
        return None

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        logger.warning(
            "Dropping record %d for %s: latitude/longitude out of physical range (lat=%s, lon=%s).",
            row_index, city, lat, lon,
        )
        return None

    acq_date = row.get("acq_date", "")
    acq_time = row.get("acq_time", "")
    timestamp_utc = derive_timestamp_utc(acq_date, acq_time)
    if timestamp_utc is None:
        logger.warning(
            "Record %d for %s has an unparseable acq_date/acq_time (%r/%r); "
            "row is kept but timestamp_utc will be empty.",
            row_index, city, acq_date, acq_time,
        )
        timestamp_utc = ""

    enriched = dict(row)
    enriched["timestamp_utc"] = timestamp_utc

    is_anomalous = False
    for field_name, (low, high) in NUMERIC_SANITY_FIELDS.items():
        if field_name not in row:
            continue
        raw_value = row[field_name]
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue  # non-numeric is fine for fields like categorical confidence
        if not (low <= value <= high):
            is_anomalous = True
            break

    return enriched, is_anomalous


def derive_timestamp_utc(acq_date: str, acq_time: str) -> str | None:
    """
    Combines FIRMS' acq_date (YYYY-MM-DD) and acq_time (HHMM, UTC per NASA
    documentation) into an explicit UTC ISO-8601 string, matching the
    timestamp_utc convention already used in openaq.py/openmeteo.py.
    Returns None if either field is missing or unparseable.
    """
    if not acq_date or acq_time is None or acq_time == "":
        return None

    try:
        date_part = datetime.strptime(acq_date.strip(), "%Y-%m-%d")
    except ValueError:
        return None

    time_str = str(acq_time).strip().zfill(4)
    if len(time_str) != 4 or not time_str.isdigit():
        return None

    hour, minute = int(time_str[:2]), int(time_str[2:])
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None

    combined = date_part.replace(hour=hour, minute=minute, tzinfo=timezone.utc)
    return combined.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_fires_csv(output_dir: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> Path:
    output_path = output_dir / "fires.csv"

    rows_sorted = sorted(rows, key=lambda r: (r.get("timestamp_utc", ""), r.get("latitude", "")))

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow(row)

    return output_path


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> Path:
    manifest_path = output_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    return manifest_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_date_arg(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}', expected format YYYY-MM-DD.") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download historical fire detections from the NASA FIRMS Area API for a city and date range.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--city", required=True, help="City name (e.g. 'Delhi'). Used for logging, output folder naming, and bbox lookup.")
    parser.add_argument("--start-date", required=True, type=parse_date_arg, help="Start date, format YYYY-MM-DD (inclusive).")
    parser.add_argument("--end-date", required=True, type=parse_date_arg, help="End date, format YYYY-MM-DD (inclusive).")
    parser.add_argument("--source", default=DEFAULT_SOURCE, choices=VALID_SP_SOURCES, help="FIRMS standard-processing (SP) source/sensor.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory to write fires.csv and manifest.json into.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity.")
    return parser


def load_dotenv_if_present() -> None:
    """
    Best-effort .env loading, consistent with openaq.py. Falls back to
    whatever is already in the process environment if python-dotenv isn't
    importable for some reason.
    """
    try:
        from dotenv import load_dotenv, find_dotenv
        env_path = find_dotenv(usecwd=True)
        if env_path:
            load_dotenv(env_path)
            logger.debug("Loaded environment variables from %s", env_path)
    except ImportError:
        logger.debug("python-dotenv not installed; relying on process environment only.")


def resolve_bbox(city: str) -> tuple[float, float, float, float]:
    bbox = CITY_BBOX_PRESETS.get(city.strip().lower())
    if bbox is None:
        raise FirmsIngestError(
            f"No built-in bounding box for city '{city}'. "
            f"Known cities: {sorted(CITY_BBOX_PRESETS.keys())}."
        )
    return bbox


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.end_date < args.start_date:
        logger.error("--end-date (%s) must not be before --start-date (%s).", args.end_date.date(), args.start_date.date())
        return 1

    try:
        bbox = resolve_bbox(args.city)
    except FirmsIngestError as exc:
        logger.error(str(exc))
        return 1

    load_dotenv_if_present()
    map_key = os.getenv("NASA_FIRMS_MAP_KEY", "")
    if not map_key:
        logger.error(
            "No NASA FIRMS MAP_KEY found. Set NASA_FIRMS_MAP_KEY in .env / the environment. "
            "See .env.example."
        )
        return 1

    area_coordinates = ",".join(f"{coord:.4f}" for coord in bbox)  # west,south,east,north
    city_slug = "".join(c if c.isalnum() else "_" for c in args.city.strip().lower())
    output_dir = args.output_dir / city_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting FIRMS ingest: city=%s source=%s bbox(west,south,east,north)=%s date_range=%s..%s output_dir=%s",
        args.city, args.source, area_coordinates, args.start_date.date(), args.end_date.date(), output_dir,
    )

    session = build_session()
    summary = CitySummary(city=args.city, source=args.source, bbox=bbox)
    all_rows: list[dict[str, str]] = []
    all_fieldnames: list[str] = []

    for chunk_start in iter_date_chunks(args.start_date, args.end_date, DAY_RANGE):
        date_str = chunk_start.strftime("%Y-%m-%d")
        url = f"{API_BASE_URL}/{map_key}/{args.source}/{area_coordinates}/{DAY_RANGE}/{date_str}"
        redacted_url = _redact_map_key(url, map_key)
        logger.info("Fetching chunk starting %s (up to %d days) for %s.", date_str, DAY_RANGE, args.city)
        logger.debug("Request URL: %s", redacted_url)

        try:
            text = fetch_chunk_csv(session, url, map_key)
            chunk_result = parse_csv_text(text, args.city)
        except FirmsIngestError as exc:
            # A single chunk failing (transient error, or a chunk that
            # happens to return no usable header) should not abort
            # collection for the rest of the date range — log loudly,
            # count it, move on. Mirrors the per-chunk resilience already
            # established in openaq.py and openmeteo.py.
            logger.warning("Skipping chunk starting %s after error: %s", date_str, exc)
            summary.chunks_failed += 1
            continue

        if not chunk_result.rows:
            logger.info("Chunk starting %s returned zero fire detections for %s (valid empty result).", date_str, args.city)
        else:
            logger.info("Chunk starting %s: %d raw records received.", date_str, len(chunk_result.rows))

        for field_name in chunk_result.fieldnames:
            if field_name not in all_fieldnames:
                all_fieldnames.append(field_name)

        for i, raw_row in enumerate(chunk_result.rows):
            result = validate_and_enrich_row(raw_row, args.city, i)
            if result is None:
                summary.rows_skipped += 1
                continue
            enriched_row, is_anomalous = result
            if is_anomalous:
                summary.anomalies += 1
            all_rows.append(enriched_row)

        summary.chunks_fetched += 1

    if "timestamp_utc" not in all_fieldnames:
        all_fieldnames.append("timestamp_utc")

    if not all_rows:
        logger.error(
            "Zero rows collected for city=%s date_range=%s..%s. "
            "This is treated as a failure, not an empty success — check the MAP_KEY, source, and date range.",
            args.city, args.start_date.date(), args.end_date.date(),
        )
        manifest = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "NASA FIRMS Area API",
            "sensor": args.source,
            "api_base_url": API_BASE_URL,
            "city": args.city,
            "bbox_west_south_east_north": area_coordinates,
            "date_from": args.start_date.date().isoformat(),
            "date_to": args.end_date.date().isoformat(),
            "chunks_fetched": summary.chunks_fetched,
            "chunks_failed": summary.chunks_failed,
            "rows_written": 0,
            "rows_skipped": summary.rows_skipped,
            "anomalies_flagged": 0,
            "columns_written": [],
            "output_file": None,
            "status": "failed_zero_rows",
        }
        write_manifest(output_dir, manifest)
        return 1

    output_path = write_fires_csv(output_dir, all_rows, all_fieldnames)
    summary.rows_written = len(all_rows)
    summary.output_file = str(output_path)
    summary.columns_written = all_fieldnames

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "NASA FIRMS Area API",
        "sensor": args.source,
        "api_base_url": API_BASE_URL,
        "city": args.city,
        "bbox_west_south_east_north": area_coordinates,
        "date_from": args.start_date.date().isoformat(),
        "date_to": args.end_date.date().isoformat(),
        "chunks_fetched": summary.chunks_fetched,
        "chunks_failed": summary.chunks_failed,
        "rows_written": summary.rows_written,
        "rows_skipped": summary.rows_skipped,
        "anomalies_flagged": summary.anomalies,
        "columns_written": summary.columns_written,
        "output_file": summary.output_file,
        "status": "success" if summary.chunks_failed == 0 else "partial_success",
    }
    manifest_path = write_manifest(output_dir, manifest)
    logger.info("Wrote manifest -> %s", manifest_path)

    logger.info(
        "Ingest complete: %d rows written, %d rows skipped, %d anomalies flagged, %d/%d chunks failed, output_dir=%s",
        summary.rows_written, summary.rows_skipped, summary.anomalies, summary.chunks_failed,
        summary.chunks_fetched + summary.chunks_failed, output_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
