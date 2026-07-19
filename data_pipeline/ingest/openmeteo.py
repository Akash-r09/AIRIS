#!/usr/bin/env python3
"""
data_pipeline/ingest/openmeteo.py

Downloads historical weather observations (temperature, humidity,
precipitation, wind, pressure) from the Open-Meteo Historical Weather API
(ERA5 reanalysis) for a target city and date range, and writes one cleaned
CSV per city into the output directory. This data is later joined against
the AQI dataset from data_pipeline/ingest/openaq.py in the feature
engineering stage (Milestone 3).

Open-Meteo API background (verified at time of writing):
  - Endpoint: https://archive-api.open-meteo.com/v1/archive
  - No API key or authentication required for non-commercial use — plain
    HTTP GET with query parameters, JSON response, CC BY 4.0 data licence.
  - Data source is ERA5 reanalysis: hourly, spatially complete (no missing
    grid cells), global coverage from January 1940 to present.
  - Takes a single (latitude, longitude) point per request, not a bounding
    box — there is no concept of "sensors" or "locations" here the way
    OpenAQ has them. One representative coordinate per city is used.
  - The response returns the full requested date range's hourly arrays in
    a single JSON payload (no pagination) — but very long ranges produce
    very large responses, so this script still chunks requests by year as
    a safety margin against oversized/timing-out responses, mirroring the
    chunking approach in openaq.py even though Open-Meteo's docs don't
    document the same timeout warning OpenAQ's do.
  - `&timezone=GMT` is passed explicitly on every request so returned
    timestamps are UTC+0 regardless of the queried location's local zone.
    "GMT" is used specifically (not "UTC") because it is the API's
    documented default value and the value shown in every example response
    in Open-Meteo's own docs — required because this data joins against
    AQI timestamps, which are also stored in UTC.
  - Invalid parameters return HTTP 400 with a JSON body of the shape
    {"error": true, "reason": "..."} — handled explicitly and distinctly
    so failures surface a clear cause rather than a generic HTTP error.

Design note on city presets: this script intentionally duplicates the
CITY_BBOX_PRESETS table from openaq.py rather than importing it. Per
docs/architecture.md, each data_pipeline/ingest/ script is self-contained
so any one of them can be run, modified, or debugged independently without
pulling in the others as a dependency. The same bounding boxes are used
here (so both scripts are querying the same physical area for the same
city), reduced to their centroid since Open-Meteo takes a single point.

Usage:
    python data_pipeline/ingest/openmeteo.py \\
        --city Delhi \\
        --start-date 2024-10-25 \\
        --end-date 2024-11-10 \\
        --output-dir data_pipeline/raw/openmeteo

Exit codes:
    0  success — at least one row of weather data was collected
    1  failure — bad arguments, API error after retries exhausted, or
       zero rows of data collected for the requested city/range
"""

from __future__ import annotations

import argparse
import csv
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

API_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Same bounding boxes as data_pipeline/ingest/openaq.py's CITY_BBOX_PRESETS
# (duplicated intentionally — see module docstring). Open-Meteo takes a
# single point, so each city's coordinate is the bbox centroid.
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

# Required hourly variables per the task spec.
DEFAULT_HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "surface_pressure",
]

# Open-Meteo's default units for these variables (Celsius, %, mm, km/h,
# degrees, hPa) — recorded per-row in the output CSV rather than assumed
# silently downstream. If --variables includes something outside this map,
# the unit is read from the API's own `hourly_units` field instead.
DEFAULT_UNITS: dict[str, str] = {
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "precipitation": "mm",
    "wind_speed_10m": "km/h",
    "wind_direction_10m": "°",
    "surface_pressure": "hPa",
}

# Loose physical sanity bounds per variable. Out-of-range values are
# flagged as anomalies in the manifest, not dropped — same philosophy as
# openaq.py: the raw layer preserves fidelity, the cleaning stage decides.
SANITY_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature_2m": (-60, 60),
    "relative_humidity_2m": (0, 100),
    "precipitation": (0, 500),
    "wind_speed_10m": (0, 250),
    "wind_direction_10m": (0, 360),
    "surface_pressure": (800, 1100),
}

DATE_CHUNK_DAYS = 366         # one request per ~year, safety margin against oversized responses
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
BACKOFF_FACTOR = 1.0

logger = logging.getLogger("openmeteo_ingest")


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class OpenMeteoIngestError(Exception):
    """Raised for any unrecoverable failure in the ingest run. Never caught
    silently — main() lets this propagate to a non-zero exit code."""


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class WeatherRecord:
    timestamp_utc: str
    city: str
    latitude: float
    longitude: float
    variable: str
    unit: str
    value: float


@dataclass
class CitySummary:
    city: str
    latitude: float
    longitude: float
    variables_matched: list[str] = field(default_factory=list)
    rows_written: int = 0
    anomalies: int = 0
    chunks_fetched: int = 0
    chunks_failed: int = 0
    output_file: str | None = None


# --------------------------------------------------------------------------
# HTTP session with retry policy
# --------------------------------------------------------------------------

def build_session() -> requests.Session:
    """
    Builds a requests.Session with automatic retry (exponential backoff)
    for connection errors and transient HTTP statuses. No auth header is
    needed — Open-Meteo's Archive API requires no API key.
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


def get_weather_json(session: requests.Session, params: dict[str, Any]) -> dict[str, Any]:
    """
    Issues a GET request to the Archive API and returns parsed JSON.
    Raises OpenMeteoIngestError on any failure that survives the retry
    policy, including Open-Meteo's documented {"error": true, "reason":
    "..."} shape for invalid parameters (HTTP 400).
    """
    try:
        response = session.get(API_BASE_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        raise OpenMeteoIngestError(f"Network error calling {API_BASE_URL} with params={params}: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenMeteoIngestError(
            f"Response from {API_BASE_URL} was not valid JSON (HTTP {response.status_code}): {exc}"
        ) from exc

    if response.status_code == 400 and payload.get("error"):
        raise OpenMeteoIngestError(
            f"Open-Meteo rejected the request: {payload.get('reason', 'no reason given')}. Params: {params}"
        )

    if response.status_code != 200:
        raise OpenMeteoIngestError(
            f"Open-Meteo API returned HTTP {response.status_code} for params={params}. "
            f"Body: {json.dumps(payload)[:500]}"
        )

    if "hourly" not in payload:
        raise OpenMeteoIngestError(
            f"Unexpected response shape: missing 'hourly' key. Keys present: {list(payload.keys())}. "
            "The Open-Meteo API may have changed."
        )

    return payload


# --------------------------------------------------------------------------
# Date chunking
# --------------------------------------------------------------------------

def iter_date_chunks(start: datetime, end: datetime, chunk_days: int) -> Iterator[tuple[datetime, datetime]]:
    """Yields (chunk_start, chunk_end) date pairs covering [start, end], each at most chunk_days wide."""
    cursor = start
    delta = timedelta(days=chunk_days - 1)  # inclusive end date within a chunk
    while cursor <= end:
        chunk_end = min(cursor + delta, end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


# --------------------------------------------------------------------------
# Fetch & parse
# --------------------------------------------------------------------------

def fetch_weather_chunk(
    session: requests.Session,
    city: str,
    latitude: float,
    longitude: float,
    chunk_start: datetime,
    chunk_end: datetime,
    variables: list[str],
) -> dict[str, Any]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": chunk_start.strftime("%Y-%m-%d"),
        "end_date": chunk_end.strftime("%Y-%m-%d"),
        "hourly": ",".join(variables),
        # "GMT" (not "UTC") is the documented default and the value shown in
        # every Open-Meteo example response for UTC+0 output. "UTC" as a
        # literal string is never demonstrated anywhere in their docs — using
        # the confirmed-correct value rather than an unverified alias.
        "timezone": "GMT",
    }
    return get_weather_json(session, params)


def parse_hourly_payload(
    payload: dict[str, Any],
    city: str,
    latitude: float,
    longitude: float,
    variables: list[str],
) -> list[WeatherRecord]:
    """
    Converts one Archive API JSON payload into validated WeatherRecords,
    one per (timestamp, parameter). Rows with a missing/non-numeric value,
    or an unparseable timestamp, are dropped with a warning, never silently
    coerced or emitted without an explicit UTC designator.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time")

    if not times:
        return []

    hourly_units = payload.get("hourly_units", {})

    records: list[WeatherRecord] = []
    for variable in variables:
        series = hourly.get(variable)
        if series is None:
            logger.warning("Variable '%s' missing entirely from response for %s — skipping.", variable, city)
            continue

        if len(series) != len(times):
            raise OpenMeteoIngestError(
                f"Response array length mismatch for variable '{variable}': "
                f"{len(series)} values vs {len(times)} timestamps. Refusing to guess alignment."
            )

        unit = hourly_units.get(variable, DEFAULT_UNITS.get(variable, ""))

        for ts, value in zip(times, series):
            if value is None or not isinstance(value, (int, float)):
                logger.warning("Dropping record for %s, variable %s, timestamp %s: missing/non-numeric value.", city, variable, ts)
                continue

            # Open-Meteo's hourly "time" strings carry no UTC designator —
            # they're only UTC because every request explicitly sets
            # timezone=GMT (see fetch_weather_chunk). Parsed and re-emitted
            # explicitly rather than assumed correct by string length.
            try:
                parsed_ts = datetime.strptime(ts, "%Y-%m-%dT%H:%M")
            except ValueError:
                try:
                    parsed_ts = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    logger.warning("Dropping record for %s, variable %s: unparseable timestamp '%s'.", city, variable, ts)
                    continue

            timestamp_utc = parsed_ts.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            records.append(WeatherRecord(
                timestamp_utc=timestamp_utc,
                city=city,
                latitude=latitude,
                longitude=longitude,
                variable=variable,
                unit=unit,
                value=float(value),
            ))

    return records


def is_anomalous(record: WeatherRecord) -> bool:
    bounds = SANITY_BOUNDS.get(record.variable)
    if bounds is None:
        return False
    low, high = bounds
    return not (low <= record.value <= high)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_city_csv(output_dir: Path, city_slug: str, records: list[WeatherRecord]) -> Path:
    filename = f"{city_slug}_weather.csv"
    output_path = output_dir / filename

    records_sorted = sorted(records, key=lambda r: (r.timestamp_utc, r.variable))

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp_utc", "city", "latitude", "longitude", "variable", "unit", "value",
        ])
        for r in records_sorted:
            writer.writerow([
                r.timestamp_utc, r.city, r.latitude, r.longitude, r.variable, r.unit, r.value,
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
        description="Download historical weather data from the Open-Meteo Archive API for a city and date range.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--city", required=True, help="City name (e.g. 'Delhi'). Used for logging, output filename, and coordinate lookup unless --latitude/--longitude are given.")
    parser.add_argument("--start-date", required=True, type=parse_date_arg, help="Start date, format YYYY-MM-DD (inclusive).")
    parser.add_argument("--end-date", required=True, type=parse_date_arg, help="End date, format YYYY-MM-DD (inclusive).")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory to write the output CSV and manifest.json into.")
    parser.add_argument("--latitude", type=float, default=None, help="Override latitude. Must be given together with --longitude. Required if --city is not in the built-in preset list.")
    parser.add_argument("--longitude", type=float, default=None, help="Override longitude. Must be given together with --latitude.")
    parser.add_argument("--variables", nargs="+", default=DEFAULT_HOURLY_VARIABLES, help="Hourly weather variables to fetch.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity.")
    return parser


def resolve_coordinates(
    city: str,
    latitude_override: float | None,
    longitude_override: float | None,
) -> tuple[float, float]:
    if latitude_override is not None or longitude_override is not None:
        if latitude_override is None or longitude_override is None:
            raise OpenMeteoIngestError("--latitude and --longitude must both be given together, not just one.")
        if not (-90 <= latitude_override <= 90) or not (-180 <= longitude_override <= 180):
            raise OpenMeteoIngestError(
                f"Invalid coordinate override: latitude={latitude_override}, longitude={longitude_override} "
                "(latitude must be -90..90, longitude -180..180)."
            )
        return (latitude_override, longitude_override)

    coords = CITY_COORDINATES.get(city.strip().lower())
    if coords is None:
        raise OpenMeteoIngestError(
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
    except OpenMeteoIngestError as exc:
        logger.error(str(exc))
        return 1

    variables = list(dict.fromkeys(args.variables))  # de-dupe, preserve order
    city_slug = "".join(c if c.isalnum() else "_" for c in args.city.strip().lower())
    output_dir = args.output_dir / city_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting Open-Meteo ingest: city=%s coordinates=(%.4f, %.4f) date_range=%s..%s variables=%s output_dir=%s",
        args.city, latitude, longitude, args.start_date.date(), args.end_date.date(), variables, output_dir,
    )

    session = build_session()
    summary = CitySummary(city=args.city, latitude=latitude, longitude=longitude)
    all_records: list[WeatherRecord] = []

    for chunk_start, chunk_end in iter_date_chunks(args.start_date, args.end_date, DATE_CHUNK_DAYS):
        logger.info("Fetching chunk %s..%s for %s.", chunk_start.date(), chunk_end.date(), args.city)
        try:
            payload = fetch_weather_chunk(session, args.city, latitude, longitude, chunk_start, chunk_end, variables)
        except OpenMeteoIngestError as exc:
            # A single chunk failing (e.g. a transient error that survived
            # retries) should not abort collection for the rest of the date
            # range — log loudly, count it, move on. Mirrors the per-chunk
            # resilience already established and merged in openaq.py.
            logger.warning("Skipping chunk %s..%s after error: %s", chunk_start.date(), chunk_end.date(), exc)
            summary.chunks_failed += 1
            continue

        chunk_records = parse_hourly_payload(payload, args.city, latitude, longitude, variables)
        if not chunk_records:
            logger.warning("Chunk %s..%s returned no usable data for %s.", chunk_start.date(), chunk_end.date(), args.city)
        else:
            logger.info("Chunk %s..%s: %d records parsed.", chunk_start.date(), chunk_end.date(), len(chunk_records))

        all_records.extend(chunk_records)
        summary.chunks_fetched += 1

    if not all_records:
        logger.error(
            "Zero rows collected for city=%s date_range=%s..%s. "
            "This is treated as a failure, not an empty success — check coordinates and date range.",
            args.city, args.start_date.date(), args.end_date.date(),
        )
        # Still write a manifest recording the failed attempt, for
        # debuggability. Schema matches the success-path manifest exactly
        # (same keys), so downstream consumers never have to special-case
        # a failed run's shape.
        manifest = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "Open-Meteo Historical Weather API (ERA5 reanalysis)",
            "api_base_url": API_BASE_URL,
            "city": args.city,
            "coordinates": {"latitude": latitude, "longitude": longitude},
            "date_from": args.start_date.date().isoformat(),
            "date_to": args.end_date.date().isoformat(),
            "variables_requested": variables,
            "variables_matched": [],
            "chunks_fetched": summary.chunks_fetched,
            "chunks_failed": summary.chunks_failed,
            "rows_written": 0,
            "anomalies_flagged": 0,
            "output_file": None,
            "status": "failed_zero_rows",
        }
        write_manifest(output_dir, manifest)
        return 1

    summary.variables_matched = sorted({r.variable for r in all_records})
    summary.anomalies = sum(1 for r in all_records if is_anomalous(r))

    output_path = write_city_csv(output_dir, city_slug, all_records)
    summary.rows_written = len(all_records)
    summary.output_file = str(output_path)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Open-Meteo Historical Weather API (ERA5 reanalysis)",
        "api_base_url": API_BASE_URL,
        "city": args.city,
        "coordinates": {"latitude": latitude, "longitude": longitude},
        "date_from": args.start_date.date().isoformat(),
        "date_to": args.end_date.date().isoformat(),
        "variables_requested": variables,
        "variables_matched": summary.variables_matched,
        "chunks_fetched": summary.chunks_fetched,
        "chunks_failed": summary.chunks_failed,
        "rows_written": summary.rows_written,
        "anomalies_flagged": summary.anomalies,
        "output_file": summary.output_file,
        "status": "success" if summary.chunks_failed == 0 else "partial_success",
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
