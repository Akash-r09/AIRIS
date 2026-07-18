#!/usr/bin/env python3
"""
data_pipeline/ingest/openaq.py

Downloads historical air quality observations (PM2.5, PM10, NO2, CO, O3)
from the OpenAQ API v3 for a target city and date range, and writes one
cleaned CSV per monitoring location into the output directory.

OpenAQ API v3 background (verified at time of writing — see "API
limitations" in the module docstring below for details that may drift):
  - v1 and v2 endpoints were retired on 2025-01-31 and now return HTTP 410
    Gone. This script uses v3 exclusively (base URL: https://api.openaq.org).
  - v3 requires an API key on every request, sent as the `X-API-Key` header.
    Unauthenticated requests are rejected. Set OPENAQ_API_KEY in .env or the
    environment (see .env.example).
  - v3 has no free-text "city" query parameter. Locations are found via a
    geospatial bounding-box query (`bbox`), so this script resolves a city
    name to a bounding box using a small built-in preset table, with a
    --bbox flag to override or add new cities.
  - Pagination: default page size 100, max 1000 (`limit` + `page` params).
  - Rate limits: exceeding them returns HTTP 429. This script retries
    transient failures (429 and 5xx) with exponential backoff, honoring a
    `Retry-After` header when the API provides one.
  - Aggregated measurement queries over long date ranges can time out
    (HTTP 408) per OpenAQ's own documentation. This script chunks each
    sensor's date range into fixed-size windows to stay well clear of that.

Usage:
    python data_pipeline/ingest/openaq.py \\
        --city Delhi \\
        --start-date 2024-10-25 \\
        --end-date 2024-11-10 \\
        --output-dir data_pipeline/raw/openaq

Exit codes:
    0  success — at least one location produced at least one row of data
    1  failure — bad arguments, missing API key, API error after retries
       exhausted, or zero rows of data collected for the whole city/range
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
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

API_BASE_URL = "https://api.openaq.org/v3"

# Bounding boxes: [min_lon, min_lat, max_lon, max_lat] (WGS 84).
# OpenAQ v3 has no free-text city search, so city names are resolved
# through this preset table. Delhi's box matches configs/settings.yaml
# (city.bbox) for consistency with the rest of the pipeline; other major
# Indian metros are included for convenience. Use --bbox to override or to
# use a city not listed here.
CITY_BBOX_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "delhi": (76.84, 28.40, 77.35, 28.88),
    "mumbai": (72.77, 18.89, 72.98, 19.27),
    "kolkata": (88.24, 22.45, 88.48, 22.66),
    "bengaluru": (77.46, 12.83, 77.75, 13.14),
    "bangalore": (77.46, 12.83, 77.75, 13.14),
    "chennai": (80.16, 12.90, 80.32, 13.23),
}

# Pollutant parameter names as reported by OpenAQ sensor metadata
# (location.sensors[i].parameter.name). Confirmed against actual sensor
# names, not assumed numeric parameter IDs, since those are more likely to
# drift than the human-readable names.
DEFAULT_PARAMETERS = ["pm25", "pm10", "no2", "co", "o3"]

# Loose physical sanity bounds per parameter, in the unit OpenAQ typically
# reports (µg/m³ for pm25/pm10/no2/o3, µg/m³ or ppm for co depending on
# provider). Values outside this range are not dropped — raw data should
# preserve fidelity — but are flagged as anomalies in the manifest so the
# cleaning stage (data_pipeline/clean/) can decide how to handle them.
SANITY_BOUNDS: dict[str, tuple[float, float]] = {
    "pm25": (0, 2000),
    "pm10": (0, 3000),
    "no2": (0, 2000),
    "co": (0, 100000),
    "o3": (0, 2000),
}

PAGE_LIMIT = 1000
DATE_CHUNK_DAYS = 30          # per OpenAQ guidance: keep aggregation windows small
MAX_LOCATION_PAGES = 20       # safety cap: 20 * 1000 = 20,000 locations max
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
BACKOFF_FACTOR = 1.0          # urllib3 backoff: {backoff_factor} * (2 ** (retry - 1))

logger = logging.getLogger("openaq_ingest")


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class OpenAQIngestError(Exception):
    """Raised for any unrecoverable failure in the ingest run. Never caught
    silently — main() lets this propagate to a non-zero exit code."""


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class SensorRecord:
    timestamp_utc: str
    timestamp_local: str
    location_id: int
    location_name: str
    latitude: float | None
    longitude: float | None
    parameter: str
    unit: str
    value: float


@dataclass
class LocationSummary:
    location_id: int
    location_name: str
    sensors_matched: list[str] = field(default_factory=list)
    rows_written: int = 0
    anomalies: int = 0
    output_file: str | None = None


# --------------------------------------------------------------------------
# HTTP session with retry policy
# --------------------------------------------------------------------------

def build_session(api_key: str) -> requests.Session:
    """
    Builds a requests.Session with:
      - the X-API-Key header set on every request
      - automatic retry with exponential backoff for connection errors and
        transient HTTP statuses (429, 500, 502, 503, 504)
      - Retry-After header respected when present (OpenAQ sends this on 429)
    """
    session = requests.Session()
    session.headers.update({
        "X-API-Key": api_key,
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
        raise_on_status=False,  # we inspect the final response ourselves
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_json(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Issues a GET request and returns parsed JSON. Raises OpenAQIngestError
    on any failure that survives the session's retry policy — this
    function never returns a partial/empty result silently.
    """
    try:
        response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as exc:
        raise OpenAQIngestError(f"Network error calling {url} with params={params}: {exc}") from exc

    if response.status_code == 401 or response.status_code == 403:
        raise OpenAQIngestError(
            f"OpenAQ API rejected the request as unauthorized (HTTP {response.status_code}). "
            "Check that OPENAQ_API_KEY is set to a valid key — see .env.example."
        )

    if response.status_code == 404:
        # Not an ingest-fatal error at the caller's level — caller decides
        # whether a 404 on this particular resource is expected (e.g. a
        # sensor that has since been retired) or should propagate.
        raise OpenAQIngestError(f"HTTP 404 Not Found for {url} with params={params}")

    if response.status_code != 200:
        raise OpenAQIngestError(
            f"OpenAQ API returned HTTP {response.status_code} for {url} "
            f"with params={params}. Body: {response.text[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenAQIngestError(f"Response from {url} was not valid JSON: {exc}") from exc

    if "results" not in payload:
        raise OpenAQIngestError(
            f"Unexpected response shape from {url}: missing 'results' key. "
            f"Keys present: {list(payload.keys())}. The OpenAQ API may have changed."
        )

    return payload


# --------------------------------------------------------------------------
# Locations & sensors
# --------------------------------------------------------------------------

def fetch_locations(session: requests.Session, bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """Paginates through /v3/locations for the given bounding box."""
    bbox_str = ",".join(f"{coord:.4f}" for coord in bbox)
    locations: list[dict[str, Any]] = []

    for page in range(1, MAX_LOCATION_PAGES + 1):
        payload = get_json(
            session,
            f"{API_BASE_URL}/locations",
            params={"bbox": bbox_str, "limit": PAGE_LIMIT, "page": page},
        )
        results = payload["results"]
        if not results:
            break

        locations.extend(results)
        logger.info("Fetched locations page %d (%d results, %d total so far).", page, len(results), len(locations))

        if len(results) < PAGE_LIMIT:
            break
    else:
        logger.warning(
            "Hit the safety cap of %d location pages — there may be more locations "
            "than were fetched. Consider narrowing --bbox.", MAX_LOCATION_PAGES
        )

    return locations


def matching_sensors(location: dict[str, Any], wanted_parameters: set[str]) -> list[dict[str, Any]]:
    """Returns the sensors on a location whose parameter name is in wanted_parameters."""
    sensors = location.get("sensors") or []
    matched = []
    for sensor in sensors:
        param_name = (sensor.get("parameter") or {}).get("name", "").lower()
        if param_name in wanted_parameters:
            matched.append(sensor)
    return matched


# --------------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------------

def iter_date_chunks(start: datetime, end: datetime, chunk_days: int) -> Iterator[tuple[datetime, datetime]]:
    """Yields (chunk_start, chunk_end) pairs covering [start, end), each at most chunk_days wide."""
    cursor = start
    delta = timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + delta, end)
        yield cursor, chunk_end
        cursor = chunk_end


def fetch_sensor_hours(
    session: requests.Session,
    sensor_id: int,
    date_from: datetime,
    date_to: datetime,
) -> list[dict[str, Any]]:
    """
    Paginates through /v3/sensors/{sensor_id}/hours across the full
    [date_from, date_to) range, internally chunked into DATE_CHUNK_DAYS
    windows to avoid the timeout OpenAQ's docs warn about on large
    aggregation queries.
    """
    all_results: list[dict[str, Any]] = []

    for chunk_start, chunk_end in iter_date_chunks(date_from, date_to, DATE_CHUNK_DAYS):
        page = 1
        while True:
            try:
                payload = get_json(
                    session,
                    f"{API_BASE_URL}/sensors/{sensor_id}/hours",
                    params={
                        "date_from": chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "date_to": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "limit": PAGE_LIMIT,
                        "page": page,
                    },
                )
            except OpenAQIngestError as exc:
                # A single sensor's chunk failing (e.g. sensor retired mid-range,
                # 404) shouldn't abort the whole city run — log and move on to
                # the next chunk/sensor. This is a deliberate, narrow exception
                # to "never silently fail": the failure IS logged loudly, just
                # not treated as fatal to the entire ingest.
                logger.warning(
                    "Skipping sensor %d chunk %s..%s after error: %s",
                    sensor_id, chunk_start.date(), chunk_end.date(), exc,
                )
                break

            results = payload["results"]
            if not results:
                break

            all_results.extend(results)

            if len(results) < PAGE_LIMIT:
                break
            page += 1

    return all_results


def parse_measurement(
    raw: dict[str, Any],
    location_id: int,
    location_name: str,
    parameter_name: str,
) -> SensorRecord | None:
    """
    Converts one raw /hours result item into a validated SensorRecord.
    Returns None (and logs) if the record is missing required fields —
    such records are dropped, never silently included with garbage values.
    """
    value = raw.get("value")
    if value is None or not isinstance(value, (int, float)):
        logger.warning("Dropping record for location %d, parameter %s: missing/non-numeric value.", location_id, parameter_name)
        return None

    period = raw.get("period") or {}
    datetime_from = period.get("datetimeFrom") or {}
    ts_utc = datetime_from.get("utc")
    ts_local = datetime_from.get("local")
    if not ts_utc:
        logger.warning("Dropping record for location %d, parameter %s: missing timestamp.", location_id, parameter_name)
        return None

    coordinates = raw.get("coordinates") or {}
    unit = (raw.get("parameter") or {}).get("units", "")

    return SensorRecord(
        timestamp_utc=ts_utc,
        timestamp_local=ts_local or ts_utc,
        location_id=location_id,
        location_name=location_name,
        latitude=coordinates.get("latitude"),
        longitude=coordinates.get("longitude"),
        parameter=parameter_name,
        unit=unit,
        value=float(value),
    )


def is_anomalous(record: SensorRecord) -> bool:
    bounds = SANITY_BOUNDS.get(record.parameter)
    if bounds is None:
        return False
    low, high = bounds
    return not (low <= record.value <= high)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_location_csv(output_dir: Path, location_id: int, location_name: str, records: list[SensorRecord]) -> Path:
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


def parse_bbox_arg(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Invalid --bbox '{value}', expected 4 comma-separated numbers: min_lon,min_lat,max_lon,max_lat."
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid --bbox '{value}': all four values must be numbers.") from exc

    if min_lon >= max_lon or min_lat >= max_lat:
        raise argparse.ArgumentTypeError(
            f"Invalid --bbox '{value}': min values must be less than max values."
        )
    return (min_lon, min_lat, max_lon, max_lat)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download historical AQI data from OpenAQ v3 for a city and date range.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--city", required=True, help="City name (e.g. 'Delhi'). Used for logging, output folder naming, and bbox lookup unless --bbox is given.")
    parser.add_argument("--start-date", required=True, type=parse_date_arg, help="Start date, format YYYY-MM-DD (inclusive).")
    parser.add_argument("--end-date", required=True, type=parse_date_arg, help="End date, format YYYY-MM-DD (exclusive — data is fetched up to but not including this date).")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory to write output CSVs and manifest.json into.")
    parser.add_argument("--bbox", type=parse_bbox_arg, default=None, help="Override the city bounding box: 'min_lon,min_lat,max_lon,max_lat'. Required if --city is not in the built-in preset list.")
    parser.add_argument("--parameters", nargs="+", default=DEFAULT_PARAMETERS, help="Pollutant parameter names to fetch.")
    parser.add_argument("--api-key", default=None, help="OpenAQ API key. Defaults to the OPENAQ_API_KEY environment variable.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity.")
    return parser


def load_dotenv_if_present() -> None:
    """
    Best-effort .env loading so this script behaves consistently whether
    run directly or via scripts/run_pipeline.sh. Uses python-dotenv, which
    is already a Milestone 0 dependency (backend/requirements.txt) — if
    it isn't importable for some reason, we fall back to whatever is
    already in the process environment rather than failing the whole
    script over an optional convenience.
    """
    try:
        from dotenv import load_dotenv, find_dotenv
        env_path = find_dotenv(usecwd=True)
        if env_path:
            load_dotenv(env_path)
            logger.debug("Loaded environment variables from %s", env_path)
    except ImportError:
        logger.debug("python-dotenv not installed; relying on process environment only.")


def resolve_bbox(city: str, bbox_override: tuple[float, float, float, float] | None) -> tuple[float, float, float, float]:
    if bbox_override is not None:
        return bbox_override

    preset = CITY_BBOX_PRESETS.get(city.strip().lower())
    if preset is None:
        raise OpenAQIngestError(
            f"No built-in bounding box for city '{city}'. "
            f"Known cities: {sorted(CITY_BBOX_PRESETS.keys())}. "
            "Pass --bbox min_lon,min_lat,max_lon,max_lat to specify one explicitly."
        )
    return preset


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.end_date <= args.start_date:
        logger.error("--end-date (%s) must be after --start-date (%s).", args.end_date.date(), args.start_date.date())
        return 1

    load_dotenv_if_present()
    api_key = args.api_key or os.environ.get("OPENAQ_API_KEY", "")
    if not api_key:
        logger.error(
            "No OpenAQ API key found. Pass --api-key or set OPENAQ_API_KEY in .env / the environment. "
            "See .env.example."
        )
        return 1

    try:
        bbox = resolve_bbox(args.city, args.bbox)
    except OpenAQIngestError as exc:
        logger.error(str(exc))
        return 1

    wanted_parameters = {p.lower() for p in args.parameters}
    city_slug = "".join(c if c.isalnum() else "_" for c in args.city.strip().lower())
    output_dir = args.output_dir / city_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting OpenAQ ingest: city=%s bbox=%s date_range=%s..%s parameters=%s output_dir=%s",
        args.city, bbox, args.start_date.date(), args.end_date.date(), sorted(wanted_parameters), output_dir,
    )

    session = build_session(api_key)

    try:
        locations = fetch_locations(session, bbox)
    except OpenAQIngestError as exc:
        logger.error("Fatal error fetching locations: %s", exc)
        return 1

    if not locations:
        logger.error(
            "No monitoring locations found for city=%s bbox=%s. "
            "Check the bounding box, or OpenAQ may have no stations registered in this area.",
            args.city, bbox,
        )
        return 1

    logger.info("Found %d candidate locations in bbox.", len(locations))

    summaries: list[LocationSummary] = []
    total_rows = 0

    for location in locations:
        location_id = location["id"]
        location_name = location.get("name", f"location_{location_id}")

        sensors = matching_sensors(location, wanted_parameters)
        if not sensors:
            logger.info("Skipping location %d (%s): no sensors match requested parameters.", location_id, location_name)
            continue

        summary = LocationSummary(location_id=location_id, location_name=location_name)
        records: list[SensorRecord] = []

        for sensor in sensors:
            sensor_id = sensor["id"]
            parameter_name = (sensor.get("parameter") or {}).get("name", "unknown").lower()

            raw_results = fetch_sensor_hours(session, sensor_id, args.start_date, args.end_date)
            if not raw_results:
                logger.info(
                    "No data for location %d (%s), sensor %d (%s) in the requested date range.",
                    location_id, location_name, sensor_id, parameter_name,
                )
                continue

            for raw in raw_results:
                record = parse_measurement(raw, location_id, location_name, parameter_name)
                if record is None:
                    continue
                if is_anomalous(record):
                    summary.anomalies += 1
                records.append(record)

            summary.sensors_matched.append(parameter_name)
            logger.info(
                "Location %d (%s), sensor %d (%s): %d records fetched.",
                location_id, location_name, sensor_id, parameter_name, len(raw_results),
            )

        if not records:
            logger.info("Skipping location %d (%s): matched sensors but zero rows of data in range.", location_id, location_name)
            continue

        output_path = write_location_csv(output_dir, location_id, location_name, records)
        summary.rows_written = len(records)
        summary.output_file = str(output_path)
        summaries.append(summary)
        total_rows += len(records)

        logger.info("Wrote %d rows for location %d (%s) -> %s", len(records), location_id, location_name, output_path)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "OpenAQ API v3",
        "api_base_url": API_BASE_URL,
        "city": args.city,
        "bbox": list(bbox),
        "date_from": args.start_date.date().isoformat(),
        "date_to": args.end_date.date().isoformat(),
        "parameters_requested": sorted(wanted_parameters),
        "locations_found_in_bbox": len(locations),
        "locations_with_data": len(summaries),
        "total_rows_written": total_rows,
        "locations": [
            {
                "location_id": s.location_id,
                "location_name": s.location_name,
                "sensors_matched": s.sensors_matched,
                "rows_written": s.rows_written,
                "anomalies_flagged": s.anomalies,
                "output_file": s.output_file,
            }
            for s in summaries
        ],
    }
    manifest_path = write_manifest(output_dir, manifest)
    logger.info("Wrote manifest -> %s", manifest_path)

    if total_rows == 0:
        logger.error(
            "Zero rows collected across %d candidate locations for city=%s date_range=%s..%s. "
            "This is treated as a failure, not an empty success — check parameters, date range, and API key.",
            len(locations), args.city, args.start_date.date(), args.end_date.date(),
        )
        return 1

    logger.info(
        "Ingest complete: %d locations written, %d total rows, output_dir=%s",
        len(summaries), total_rows, output_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
