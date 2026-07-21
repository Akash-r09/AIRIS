#!/usr/bin/env python3
"""
data_pipeline/ingest/openmeteo_air.py

Downloads historical air quality data (PM2.5, PM10, NO2, CO, O3) from the
Open-Meteo Air Quality API for a target city and date range, and writes
output in the same schema and file layout data_pipeline/ingest/openaq.py
used to produce -- this is a drop-in replacement for that module, not a
new pipeline stage. See "Why OpenAQ was replaced" and "Schema compatibility"
below for the reasoning.

=============================================================================
Why OpenAQ was replaced
=============================================================================
OpenAQ v3's REST API was fully implemented (locations -> sensors ->
/v3/sensors/{id}/hours, with the correct datetime_from/datetime_to
parameters). After that was working, every Delhi sensor -- both older
sensors whose own metadata reports a datetimeLast of 2016-2018, and
sensors that otherwise look current -- returned zero measurement rows for
the requested historical window. Locations and sensor metadata exist;
underlying measurement data does not, for the period AIRIS needs. This was
confirmed to be a data-availability gap in OpenAQ's own systems, not a bug
in the ingestion code. Open-Meteo Air Quality API (CAMS reanalysis/
forecast data) is used instead: it has no such gap for the Nov 2024 Delhi
window (data available from August 2022 onwards for the global domain that
covers India), needs no API key, and has no per-sensor discovery step at
all -- one HTTP request per date-chunk returns the full time series for a
point.

=============================================================================
Schema compatibility (read before changing anything downstream)
=============================================================================
The task that produced this module also proposed a different, wide-format
output schema (timestamp/latitude/longitude/pm25/pm10/.../station_id) and a
different default output directory (data_pipeline/raw/air_quality). That
schema is NOT used here, deliberately: data_pipeline/preprocessing/clean.py
and validators.py are frozen (explicitly not to be modified) and their
load_and_clean_openaq() function hard-requires the *original* long-format
OpenAQ schema -- specifically the columns {timestamp_utc, parameter, value}
(validators.OPENAQ_REQUIRED_COLUMNS) plus a "location_id" column referenced
directly in clean.py's deduplication call, plus files matching the glob
pattern "location_*.csv" inside a directory the *caller* passes as
--output-dir. Producing the wide-format schema instead would silently break
the frozen preprocessing stage -- exactly what "preserve compatibility"
rules out. This module therefore reproduces openaq.py's original output
schema and file-naming convention exactly, so it is a true drop-in
replacement requiring zero changes anywhere else in the pipeline.

Practical consequence: to remain compatible with merge.py (frozen, hardcodes
the "openaq" subfolder name in resolve_city_dirs()), invoke this script
with `--output-dir data_pipeline/raw/openaq` -- not `data_pipeline/raw/
air_quality` -- unless and until the orchestration layer is deliberately
updated to point elsewhere. --output-dir remains a required, uninterpreted
CLI argument (exactly like openaq.py's own); this module does not hardcode
a default, so operators retain control, but the default needed for
zero-downstream-changes compatibility is stated explicitly here.

=============================================================================
Open-Meteo Air Quality API background (verified at time of writing)
=============================================================================
  - Endpoint: https://air-quality-api.open-meteo.com/v1/air-quality -- a
    different subdomain from the historical *weather* archive API used by
    openmeteo.py; same general request/response shape, separate service.
  - No API key required for non-commercial use.
  - No pagination: a single request returns the complete requested
    date-chunk's hourly arrays directly in one JSON payload.
  - Variable names are Open-Meteo's own: pm10, pm2_5, carbon_monoxide,
    nitrogen_dioxide, ozone -- NOT OpenAQ's short names (pm25, co, no2,
    o3). PARAMETER_NAME_MAP translates Open-Meteo's names to OpenAQ's
    original short names when writing output, so the "parameter" column
    -- and therefore the "aqi_*" columns clean.py's pivot produces -- are
    character-for-character identical to what the original OpenAQ-based
    pipeline would have produced.
  - `&timezone=GMT` is passed explicitly (not "UTC" -- see openmeteo.py's
    own reviewed fix for why "GMT" is the confirmed-documented value).
  - Historical coverage: the CAMS Global Atmospheric Composition Forecast
    (the domain covering India/Delhi, since CAMS European coverage is
    Europe-only) is available from August 2022 onwards at 3-hourly native
    temporal resolution and ~45km spatial resolution -- coarser than the
    11km/hourly European domain. Open-Meteo's API still returns an hourly
    "time" array regardless of the underlying model's native cadence; see
    "Known limitations" in the final summary for what this means in
    practice.
  - Takes a single (latitude, longitude) point per request, not a bounding
    box -- same constraint as openmeteo.py's weather module. One
    representative coordinate (the city bbox centroid) is used per city.

Usage:
    python data_pipeline/ingest/openmeteo_air.py \\
        --city Delhi \\
        --start-date 2024-10-25 \\
        --end-date 2024-11-10 \\
        --output-dir data_pipeline/raw/openaq

Exit codes:
    0  success — at least one row of air quality data was collected
    1  failure — bad arguments, API error after retries exhausted, or
       zero rows of data collected for the requested city/range
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
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

API_BASE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Identical to CITY_BBOX_PRESETS in openaq.py / openmeteo.py / firms.py /
# osm.py -- duplicated intentionally per each ingest module's established
# precedent of self-containment rather than cross-importing siblings.
CITY_BBOX_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "delhi": (76.84, 28.40, 77.35, 28.88),
    "mumbai": (72.77, 18.89, 72.98, 19.27),
    "kolkata": (88.24, 22.45, 88.48, 22.66),
    "bengaluru": (77.46, 12.83, 77.75, 13.14),
    "bangalore": (77.46, 12.83, 77.75, 13.14),
    "chennai": (80.16, 12.90, 80.32, 13.23),
}


def _bbox_centroid(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Returns (latitude, longitude) center point of a [min_lon, min_lat, max_lon, max_lat] bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return ((min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0)


CITY_COORDINATES: dict[str, tuple[float, float]] = {
    city: _bbox_centroid(bbox) for city, bbox in CITY_BBOX_PRESETS.items()
}

# Open-Meteo's own variable names (used in the API request).
DEFAULT_OPENMETEO_VARIABLES = ["pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide", "ozone"]

# Translates Open-Meteo variable names to OpenAQ's original short parameter
# names, so output is character-for-character compatible with what
# openaq.py produced -- see "Schema compatibility" in the module docstring.
PARAMETER_NAME_MAP: dict[str, str] = {
    "pm10": "pm10",
    "pm2_5": "pm25",
    "carbon_monoxide": "co",
    "nitrogen_dioxide": "no2",
    "ozone": "o3",
}

# Loose physical sanity bounds per OpenAQ-style parameter name, in µg/m³
# (Open-Meteo reports all five of these in µg/m³). Anomalies are flagged
# in the manifest, never dropped -- same philosophy as every other ingest
# module in this project.
SANITY_BOUNDS: dict[str, tuple[float, float]] = {
    "pm25": (0, 2000),
    "pm10": (0, 3000),
    "no2": (0, 2000),
    "co": (0, 100000),
    "o3": (0, 2000),
}

DATE_CHUNK_DAYS = 366
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
BACKOFF_FACTOR = 1.0

logger = logging.getLogger("openmeteo_air_ingest")


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class OpenMeteoAirIngestError(Exception):
    """Raised for any unrecoverable failure in a single chunk fetch/parse.
    Caught per-chunk in main() so one bad chunk doesn't abort the whole
    run — never caught silently."""


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class AirQualityRecord:
    timestamp_utc: str
    timestamp_local: str
    location_id: int
    location_name: str
    latitude: float
    longitude: float
    parameter: str
    unit: str
    value: float


@dataclass
class CitySummary:
    city: str
    location_id: int
    latitude: float
    longitude: float
    parameters_matched: list[str] = field(default_factory=list)
    rows_written: int = 0
    anomalies: int = 0
    chunks_fetched: int = 0
    chunks_failed: int = 0
    output_file: str | None = None


# --------------------------------------------------------------------------
# Deterministic synthetic location ID
# --------------------------------------------------------------------------

def generate_deterministic_location_id(latitude: float, longitude: float) -> int:
    """
    Open-Meteo's Air Quality API has no concept of a physical monitoring
    station or station ID at all -- it returns a modeled value for the
    grid cell nearest the requested coordinate. The frozen preprocessing
    pipeline's schema still expects a location_id column (originally one
    real station ID per OpenAQ monitoring station, used for grouping and
    deduplication). A deterministic pseudo-ID is derived here from the
    coordinate via SHA-256 -- explicitly not Python's built-in hash(),
    which is randomized per-process by default (PYTHONHASHSEED) and would
    produce a different, non-reproducible ID on every run. The same
    (4-decimal-rounded) coordinate always produces the same ID, so re-runs
    of this script for the same city are stable and comparable.
    """
    coord_key = f"{latitude:.4f},{longitude:.4f}"
    digest = hashlib.sha256(coord_key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


# --------------------------------------------------------------------------
# HTTP session with retry policy
# --------------------------------------------------------------------------

def build_session() -> requests.Session:
    """
    Builds a requests.Session with automatic retry (exponential backoff)
    for connection errors and transient HTTP statuses. No auth header is
    needed -- Open-Meteo's Air Quality API requires no API key.
    """
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
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


def get_air_quality_json(session: requests.Session, params: dict[str, Any]) -> dict[str, Any]:
    """
    Issues a GET request and returns parsed JSON. Raises
    OpenMeteoAirIngestError on any failure that survives the retry policy,
    including Open-Meteo's documented {"error": true, "reason": "..."}
    shape for invalid parameters (HTTP 400).
    """
    try:
        response = session.get(API_BASE_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        raise OpenMeteoAirIngestError(f"Network error calling {API_BASE_URL} with params={params}: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenMeteoAirIngestError(
            f"Response from {API_BASE_URL} was not valid JSON (HTTP {response.status_code}): {exc}"
        ) from exc

    if response.status_code == 400 and payload.get("error"):
        raise OpenMeteoAirIngestError(
            f"Open-Meteo Air Quality API rejected the request: {payload.get('reason', 'no reason given')}. Params: {params}"
        )

    if response.status_code != 200:
        raise OpenMeteoAirIngestError(
            f"Open-Meteo Air Quality API returned HTTP {response.status_code} for params={params}. "
            f"Body: {json.dumps(payload)[:500]}"
        )

    if "hourly" not in payload:
        raise OpenMeteoAirIngestError(
            f"Unexpected response shape: missing 'hourly' key. Keys present: {list(payload.keys())}. "
            "The Open-Meteo Air Quality API may have changed."
        )

    return payload


# --------------------------------------------------------------------------
# Date chunking
# --------------------------------------------------------------------------

def iter_date_chunks(start: datetime, end: datetime, chunk_days: int) -> Iterator[tuple[datetime, datetime]]:
    """Yields (chunk_start, chunk_end) date pairs covering [start, end], each at most chunk_days wide."""
    cursor = start
    delta = timedelta(days=chunk_days - 1)
    while cursor <= end:
        chunk_end = min(cursor + delta, end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


# --------------------------------------------------------------------------
# Fetch & parse
# --------------------------------------------------------------------------

def fetch_air_quality_chunk(
    session: requests.Session,
    latitude: float,
    longitude: float,
    chunk_start: datetime,
    chunk_end: datetime,
    variables: list[str],
    domain: str,
) -> dict[str, Any]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": chunk_start.strftime("%Y-%m-%d"),
        "end_date": chunk_end.strftime("%Y-%m-%d"),
        "hourly": ",".join(variables),
        "domains": domain,
        # "GMT" (not "UTC") is Open-Meteo's documented default and the
        # value shown in every example response -- see openmeteo.py's
        # reviewed fix for the same reasoning, which applies identically
        # to this sibling API.
        "timezone": "GMT",
    }
    return get_air_quality_json(session, params)


def parse_hourly_payload(
    payload: dict[str, Any],
    city: str,
    location_id: int,
    location_name: str,
    latitude: float,
    longitude: float,
    variables: list[str],
) -> list[AirQualityRecord]:
    """
    Converts one Air Quality API JSON payload into validated
    AirQualityRecords, one per (timestamp, parameter). Rows with a
    missing/non-numeric value, or an unparseable timestamp, are dropped
    with a warning -- never silently coerced or emitted without an
    explicit UTC designator.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time")

    if not times:
        return []

    hourly_units = payload.get("hourly_units", {})

    records: list[AirQualityRecord] = []
    for variable in variables:
        series = hourly.get(variable)
        if series is None:
            logger.warning("Variable '%s' missing entirely from response for %s -- skipping.", variable, city)
            continue

        if len(series) != len(times):
            raise OpenMeteoAirIngestError(
                f"Response array length mismatch for variable '{variable}': "
                f"{len(series)} values vs {len(times)} timestamps. Refusing to guess alignment."
            )

        unit = hourly_units.get(variable, "µg/m³")
        parameter_name = PARAMETER_NAME_MAP.get(variable, variable)

        for ts, value in zip(times, series):
            if value is None or not isinstance(value, (int, float)):
                logger.warning("Dropping record for %s, variable %s, timestamp %s: missing/non-numeric value.", city, variable, ts)
                continue

            # Open-Meteo's hourly "time" strings carry no UTC designator --
            # they're only UTC because every request explicitly sets
            # timezone=GMT. Parsed and re-emitted explicitly rather than
            # assumed correct by string length (see openmeteo.py's
            # reviewed fix for the same issue in the sibling weather module).
            try:
                parsed_ts = datetime.strptime(ts, "%Y-%m-%dT%H:%M")
            except ValueError:
                try:
                    parsed_ts = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    logger.warning("Dropping record for %s, variable %s: unparseable timestamp '%s'.", city, variable, ts)
                    continue

            timestamp_utc = parsed_ts.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            # No per-station local-time metadata exists for a modeled grid
            # cell the way OpenAQ's own station records had -- timestamp_local
            # is set equal to timestamp_utc since the request explicitly
            # asks for timezone=GMT (UTC+0). This is a disclosed
            # simplification, not a fabricated local time.
            timestamp_local = timestamp_utc

            records.append(AirQualityRecord(
                timestamp_utc=timestamp_utc,
                timestamp_local=timestamp_local,
                location_id=location_id,
                location_name=location_name,
                latitude=latitude,
                longitude=longitude,
                parameter=parameter_name,
                unit=unit,
                value=float(value),
            ))

    return records


def is_anomalous(record: AirQualityRecord) -> bool:
    bounds = SANITY_BOUNDS.get(record.parameter)
    if bounds is None:
        return False
    low, high = bounds
    return not (low <= record.value <= high)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_location_csv(output_dir: Path, location_id: int, location_name: str, records: list[AirQualityRecord]) -> Path:
    """
    Writes output in the exact schema and file-naming convention
    openaq.py used (location_<id>_<name>.csv, same 9 columns in the same
    order) so data_pipeline/preprocessing/clean.py's load_and_clean_openaq()
    consumes it without any changes.
    """
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in location_name.strip()) or "unnamed"
    filename = f"location_{location_id}_{safe_name}.csv"
    output_path = output_dir / filename

    records_sorted = sorted(records, key=lambda r: (r.timestamp_utc, r.parameter))

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp_utc", "timestamp_local", "location_id", "location_name",
            "latitude", "longitude", "parameter", "unit", "value",
        ])
        for r in records_sorted:
            writer.writerow([
                r.timestamp_utc, r.timestamp_local, r.location_id, r.location_name,
                r.latitude, r.longitude, r.parameter, r.unit, r.value,
            ])

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
        description="Download historical air quality data from Open-Meteo Air Quality API, "
                     "in a schema compatible with the original OpenAQ ingestion module.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--city", required=True, help="City name (e.g. 'Delhi'). Used for logging, output folder naming, and coordinate lookup.")
    parser.add_argument("--start-date", required=True, type=parse_date_arg, help="Start date, format YYYY-MM-DD (inclusive).")
    parser.add_argument("--end-date", required=True, type=parse_date_arg, help="End date, format YYYY-MM-DD (inclusive).")
    parser.add_argument("--output-dir", required=True, type=Path,
                         help="Directory to write output CSVs and manifest.json into. "
                              "Pass 'data_pipeline/raw/openaq' to remain compatible with the frozen "
                              "preprocessing pipeline (see module docstring) -- NOT 'data_pipeline/raw/air_quality'.")
    parser.add_argument("--latitude", type=float, default=None, help="Override latitude. Must be given together with --longitude.")
    parser.add_argument("--longitude", type=float, default=None, help="Override longitude. Must be given together with --latitude.")
    parser.add_argument("--parameters", nargs="+", default=DEFAULT_OPENMETEO_VARIABLES,
                         help="Open-Meteo variable names to fetch (not OpenAQ short names).")
    parser.add_argument("--domain", default="auto", choices=["auto", "cams_europe", "cams_global"],
                         help="CAMS model domain. 'auto' lets Open-Meteo choose based on coordinates "
                              "(cams_global for India/Delhi).")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity.")
    return parser


def resolve_coordinates(
    city: str,
    latitude_override: float | None,
    longitude_override: float | None,
) -> tuple[float, float]:
    if latitude_override is not None or longitude_override is not None:
        if latitude_override is None or longitude_override is None:
            raise OpenMeteoAirIngestError("--latitude and --longitude must both be given together, not just one.")
        if not (-90 <= latitude_override <= 90) or not (-180 <= longitude_override <= 180):
            raise OpenMeteoAirIngestError(
                f"Invalid coordinate override: latitude={latitude_override}, longitude={longitude_override} "
                "(latitude must be -90..90, longitude -180..180)."
            )
        return (latitude_override, longitude_override)

    coords = CITY_COORDINATES.get(city.strip().lower())
    if coords is None:
        raise OpenMeteoAirIngestError(
            f"No built-in coordinates for city '{city}'. "
            f"Known cities: {sorted(CITY_COORDINATES.keys())}. "
            "Pass --latitude and --longitude to specify a location explicitly."
        )
    return coords


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
        latitude, longitude = resolve_coordinates(args.city, args.latitude, args.longitude)
    except OpenMeteoAirIngestError as exc:
        logger.error(str(exc))
        return 1

    location_id = generate_deterministic_location_id(latitude, longitude)
    location_name = args.city.strip()
    variables = list(dict.fromkeys(args.parameters))
    city_slug = "".join(c if c.isalnum() else "_" for c in args.city.strip().lower())
    output_dir = args.output_dir / city_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting Open-Meteo Air Quality ingest: city=%s coordinates=(%.4f, %.4f) "
        "synthetic_location_id=%d domain=%s date_range=%s..%s variables=%s output_dir=%s",
        args.city, latitude, longitude, location_id, args.domain,
        args.start_date.date(), args.end_date.date(), variables, output_dir,
    )

    session = build_session()
    summary = CitySummary(city=args.city, location_id=location_id, latitude=latitude, longitude=longitude)
    all_records: list[AirQualityRecord] = []

    for chunk_start, chunk_end in iter_date_chunks(args.start_date, args.end_date, DATE_CHUNK_DAYS):
        logger.info("Fetching chunk %s..%s for %s.", chunk_start.date(), chunk_end.date(), args.city)
        try:
            payload = fetch_air_quality_chunk(session, latitude, longitude, chunk_start, chunk_end, variables, args.domain)
        except OpenMeteoAirIngestError as exc:
            # A single chunk failing (e.g. a transient error that survived
            # retries) should not abort collection for the rest of the date
            # range -- log loudly, count it, move on. Mirrors the per-chunk
            # resilience already established and reviewed in openmeteo.py.
            logger.warning("Skipping chunk %s..%s after error: %s", chunk_start.date(), chunk_end.date(), exc)
            summary.chunks_failed += 1
            continue

        chunk_records = parse_hourly_payload(payload, args.city, location_id, location_name, latitude, longitude, variables)
        if not chunk_records:
            logger.warning("Chunk %s..%s returned no usable data for %s.", chunk_start.date(), chunk_end.date(), args.city)
        else:
            logger.info("Chunk %s..%s: %d records parsed.", chunk_start.date(), chunk_end.date(), len(chunk_records))

        all_records.extend(chunk_records)
        summary.chunks_fetched += 1

    if not all_records:
        logger.error(
            "Zero rows collected for city=%s date_range=%s..%s. "
            "This is treated as a failure, not an empty success -- check coordinates, domain, and date range.",
            args.city, args.start_date.date(), args.end_date.date(),
        )
        manifest = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "Open-Meteo Air Quality API",
            "api_base_url": API_BASE_URL,
            "replaces": "data_pipeline/ingest/openaq.py (OpenAQ v3 -- deprecated due to zero measurement "
                        "availability for the target historical window)",
            "city": args.city,
            "domain_requested": args.domain,
            "date_from": args.start_date.date().isoformat(),
            "date_to": args.end_date.date().isoformat(),
            "parameters_requested": variables,
            "locations_found_in_bbox": 1,
            "locations_with_data": 0,
            "total_rows_written": 0,
            "chunks_fetched": summary.chunks_fetched,
            "chunks_failed": summary.chunks_failed,
            "locations": [],
            "status": "failed_zero_rows",
        }
        write_manifest(output_dir, manifest)
        return 1

    summary.parameters_matched = sorted({r.parameter for r in all_records})
    summary.anomalies = sum(1 for r in all_records if is_anomalous(r))

    output_path = write_location_csv(output_dir, location_id, location_name, all_records)
    summary.rows_written = len(all_records)
    summary.output_file = str(output_path)

    status = "success" if summary.chunks_failed == 0 else "partial_success"

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Open-Meteo Air Quality API",
        "api_base_url": API_BASE_URL,
        "replaces": "data_pipeline/ingest/openaq.py (OpenAQ v3 -- deprecated due to zero measurement "
                    "availability for the target historical window)",
        "city": args.city,
        "domain_requested": args.domain,
        "date_from": args.start_date.date().isoformat(),
        "date_to": args.end_date.date().isoformat(),
        "parameters_requested": variables,
        "locations_found_in_bbox": 1,
        "locations_with_data": 1,
        "total_rows_written": summary.rows_written,
        "chunks_fetched": summary.chunks_fetched,
        "chunks_failed": summary.chunks_failed,
        "locations": [
            {
                "location_id": location_id,
                "location_id_source": "deterministic SHA-256 hash of (latitude, longitude) -- see generate_deterministic_location_id()",
                "location_name": location_name,
                "latitude": latitude,
                "longitude": longitude,
                "parameters_matched": summary.parameters_matched,
                "rows_written": summary.rows_written,
                "anomalies_flagged": summary.anomalies,
                "output_file": summary.output_file,
            }
        ],
        "status": status,
    }
    manifest_path = write_manifest(output_dir, manifest)
    logger.info("Wrote manifest -> %s", manifest_path)

    logger.info(
        "Ingest complete: %d rows written for %s, %d anomalies flagged, %d/%d chunks failed, output_dir=%s",
        summary.rows_written, args.city, summary.anomalies, summary.chunks_failed,
        summary.chunks_fetched + summary.chunks_failed, output_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
