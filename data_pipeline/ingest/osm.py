#!/usr/bin/env python3
"""
data_pipeline/ingest/osm.py

Downloads static geographic features (roads, industrial land use, railways,
power plants, parks, water bodies, etc.) from OpenStreetMap via the
Overpass API for a target city's bounding box, and writes one CSV into the
output directory. This is purely raw data collection — no feature
engineering, no ML — the output is later joined against AQI, weather, and
fire data in the feature engineering stage (Milestone 3).

Overpass API background (verified at time of writing):
  - Official public endpoint: https://overpass-api.de/api/interpreter
  - No API key or authentication required — plain HTTP POST with an
    Overpass QL query in the request body, JSON response via [out:json].
  - Bounding boxes in Overpass QL use (south, west, north, east) order —
    this is different from the (west,south,east,north)/(min_lon,min_lat,
    max_lon,max_lat) ordering used by openaq.py, openmeteo.py, and
    firms.py, so CITY_BBOX_PRESETS (reused verbatim from those modules) is
    explicitly reordered before being embedded in the query.
  - "out center;" is used so a single query returns full tags (the
    default body verbosity already includes tags) for every element (nodes, ways, relations) plus a computed centroid
    coordinate for ways/relations that aren't already single points —
    this avoids needing a geometry library (GeoPandas/Shapely) to reduce
    polygons/lines to representative points, matching the constraint that
    this module performs no processing beyond raw collection.
  - A single combined query (one HTTP request per city) covers every
    requested feature category, per the frozen implementation note to
    avoid repeated calls.
  - Overpass has no formal API-key-based rate limit, but is a shared
    public resource; a descriptive User-Agent is set as a courtesy, and
    the retry policy backs off on 429/5xx the same way the other ingest
    modules do, since an overloaded public instance can return either.

Design note on feature-tag mapping: a handful of the requested categories
("truck terminal", "bus depot") have no single standardized OSM tag.
"bus depot" is approximated with amenity=bus_station, the closest standard
tag; "truck terminal" has no dedicated tag at all and is not separately
queried — it is only indirectly covered by the general industrial landuse
category. This is stated explicitly rather than inventing a non-standard
tag key that would silently return zero results.

Usage:
    python data_pipeline/ingest/osm.py \\
        --city Delhi \\
        --output-dir data_pipeline/raw/osm

Exit codes:
    0  success or partial_success — at least one feature was collected
    1  failure — bad arguments, network/API error, or zero features
       collected for the requested city
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

OVERPASS_ENDPOINT = os.getenv(
    "OVERPASS_ENDPOINT",
    "https://overpass.kumi.systems/api/interpreter",
)

# Identical to CITY_BBOX_PRESETS in openaq.py / openmeteo.py / firms.py —
# reused verbatim, not redefined, per the frozen city-support requirement.
# Stored as (min_lon, min_lat, max_lon, max_lat); reordered to Overpass's
# (south, west, north, east) convention in resolve_bbox_overpass() below.
CITY_BBOX_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "delhi": (76.84, 28.40, 77.35, 28.88),
    "mumbai": (72.77, 18.89, 72.98, 19.27),
    "kolkata": (88.24, 22.45, 88.48, 22.66),
    "bengaluru": (77.46, 12.83, 77.75, 13.14),
    "bangalore": (77.46, 12.83, 77.75, 13.14),
    "chennai": (80.16, 12.90, 80.32, 13.23),
}

OVERPASS_QUERY_TIMEOUT_SECONDS = 90   # server-side execution budget, embedded in the query itself
REQUEST_TIMEOUT_SECONDS = 120         # HTTP client timeout, kept above the query timeout above
MAX_RETRIES = 5
BACKOFF_FACTOR = 2.0                  # Overpass's shared public instance warrants a gentler retry cadence

logger = logging.getLogger("osm_ingest")


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class OSMIngestError(Exception):
    """Raised for any unrecoverable failure in the ingest run. Never caught
    silently — main() lets this propagate to a non-zero exit code."""


# --------------------------------------------------------------------------
# Feature taxonomy
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureSpec:
    category: str
    feature_type: str
    element_types: tuple[str, ...]  # any of "node", "way", "relation"
    tag_key: str
    tag_value: str


# Every category the task lists is represented here by its closest
# standard OSM tag. See the module docstring for the two categories
# ("truck terminal", "bus depot") that don't map to a single standard tag.
FEATURE_SPECS: list[FeatureSpec] = [
    FeatureSpec("roads", "motorway", ("way",), "highway", "motorway"),
    FeatureSpec("roads", "trunk", ("way",), "highway", "trunk"),
    FeatureSpec("roads", "primary", ("way",), "highway", "primary"),
    FeatureSpec("roads", "secondary", ("way",), "highway", "secondary"),
    FeatureSpec("industrial", "industrial_landuse", ("way", "relation"), "landuse", "industrial"),
    FeatureSpec("industrial", "industrial_building", ("way",), "building", "industrial"),
    FeatureSpec("industrial", "construction", ("way",), "landuse", "construction"),
    FeatureSpec("industrial", "factory", ("way", "node"), "man_made", "works"),
    FeatureSpec("industrial", "power_plant", ("way", "node"), "power", "plant"),
    FeatureSpec("industrial", "landfill", ("way",), "landuse", "landfill"),
    FeatureSpec("transport", "railway", ("way",), "railway", "rail"),
    FeatureSpec("transport", "bus_station", ("way", "node"), "amenity", "bus_station"),
    FeatureSpec("green_space", "park", ("way",), "leisure", "park"),
    FeatureSpec("green_space", "forest", ("way", "relation"), "landuse", "forest"),
    FeatureSpec("green_space", "wood", ("way", "relation"), "natural", "wood"),
    FeatureSpec("green_space", "grass", ("way",), "landuse", "grass"),
    FeatureSpec("green_space", "garden", ("way", "node"), "leisure", "garden"),
    FeatureSpec("water", "water_body", ("way", "relation"), "natural", "water"),
    FeatureSpec("water", "river", ("way", "relation"), "waterway", "river"),
]

CSV_FIELDNAMES = [
    "osm_id", "element_type", "latitude", "longitude",
    "feature_category", "feature_type", "name", "tags_json",
]


def classify_element(tags: dict[str, str]) -> tuple[str, str]:
    """
    Maps an element's raw OSM tags back to (feature_category, feature_type)
    by checking against FEATURE_SPECS in order. An element could in
    principle match more than one spec (e.g. landuse=industrial AND
    building=industrial on the same way) — the first match wins,
    deterministically. An element matching none of the specs (shouldn't
    happen since the query only requests matching elements, but handled
    defensively) is preserved, not dropped, under "other"/"unclassified".
    """
    for spec in FEATURE_SPECS:
        if tags.get(spec.tag_key) == spec.tag_value:
            return spec.category, spec.feature_type
    return "other", "unclassified"


def build_overpass_query(south: float, west: float, north: float, east: float) -> str:
    """
    Builds a single Overpass QL query covering every FEATURE_SPECS entry
    for the given bounding box — one HTTP request per city, per the
    frozen implementation note against unnecessary repeated calls.
    "out center;" returns full tags (default body verbosity) plus a
    computed centroid for ways/relations, so no geometry library is
    needed to reduce polygons/lines to a representative point.
    """
    lines = []
    for spec in FEATURE_SPECS:
        for element_type in spec.element_types:
            lines.append(
                f'  {element_type}["{spec.tag_key}"="{spec.tag_value}"]'
                f"({south:.6f},{west:.6f},{north:.6f},{east:.6f});"
            )

    body = "\n".join(lines)
    return (
        f"[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_SECONDS}];\n"
        f"(\n{body}\n);\n"
        f"out center;"
    )


# --------------------------------------------------------------------------
# HTTP session with retry policy
# --------------------------------------------------------------------------

def build_session() -> requests.Session:
    """
    Builds a requests.Session with automatic retry (exponential backoff)
    for connection errors and transient HTTP statuses. No auth header is
    needed — Overpass requires no API key.
    """
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "AIRIS-data-pipeline/0.1 (+hackathon prototype; static OSM feature collection)",
    })

    retry = Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        status=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        allowed_methods=["POST"],
        raise_on_status=False,  # final response is inspected explicitly below
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_overpass_json(session: requests.Session, query: str) -> dict[str, Any]:
    """
    POSTs the Overpass QL query and returns parsed JSON. Raises
    OSMIngestError on any failure that survives the retry policy,
    including a 200 response whose body isn't valid JSON, or valid JSON
    missing the expected "elements" key (Overpass sometimes returns an
    HTML error page with a 200 status for a rejected query).
    """
    try:
        response = session.post(
            OVERPASS_ENDPOINT,
            data={"data": query},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout as exc:
        raise OSMIngestError(f"Request to Overpass API timed out after {REQUEST_TIMEOUT_SECONDS}s: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise OSMIngestError(f"Network error calling Overpass API: {exc}") from exc

    if response.status_code != 200:
        raise OSMIngestError(
            f"Overpass API returned HTTP {response.status_code}. Body: {response.text[:500]!r}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OSMIngestError(
            f"Overpass response was not valid JSON: {exc}. Body preview: {response.text[:500]!r}"
        ) from exc

    if "elements" not in payload:
        raise OSMIngestError(
            f"Unexpected Overpass response shape: missing 'elements' key. "
            f"Keys present: {list(payload.keys())}."
        )

    return payload


# --------------------------------------------------------------------------
# Parsing & validation
# --------------------------------------------------------------------------

def extract_coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    """
    Returns (latitude, longitude) for an element, preferring direct
    lat/lon (nodes) and falling back to the "center" object Overpass
    computes for ways/relations when "out center" is used. Returns None
    if neither is present or the values aren't valid numbers/ranges —
    callers log and skip such elements rather than guessing a location.
    """
    lat, lon = element.get("lat"), element.get("lon")
    if lat is None or lon is None:
        center = element.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")

    if lat is None or lon is None:
        return None

    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None

    if not (-90 <= lat_f <= 90) or not (-180 <= lon_f <= 180):
        return None

    return lat_f, lon_f


def parse_elements(elements: list[dict[str, Any]], city: str) -> tuple[list[dict[str, Any]], int]:
    """
    Converts raw Overpass elements into CSV-ready row dicts. Returns
    (rows, skipped_count). Every skip is logged with its reason — an
    element is only ever skipped for missing/invalid id or coordinates,
    never for an unrecognized tag combination (those are kept under
    category="other").
    """
    rows: list[dict[str, Any]] = []
    skipped = 0

    for i, element in enumerate(elements):
        osm_id = element.get("id")
        element_type = element.get("type")

        if osm_id is None or element_type not in ("node", "way", "relation"):
            logger.warning("Skipping element %d for %s: missing id or unrecognized type (%r).", i, city, element_type)
            skipped += 1
            continue

        coords = extract_coordinates(element)
        if coords is None:
            logger.warning(
                "Skipping element %d (%s/%s) for %s: no usable geometry "
                "(neither direct lat/lon nor a center coordinate).",
                i, element_type, osm_id, city,
            )
            skipped += 1
            continue

        latitude, longitude = coords
        tags = element.get("tags") or {}
        category, feature_type = classify_element(tags)

        rows.append({
            "osm_id": osm_id,
            "element_type": element_type,
            "latitude": latitude,
            "longitude": longitude,
            "feature_category": category,
            "feature_type": feature_type,
            "name": tags.get("name", ""),
            "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
        })

    return rows, skipped


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_features_csv(output_dir: Path, rows: list[dict[str, Any]]) -> Path:
    output_path = output_dir / "osm_features.csv"

    rows_sorted = sorted(rows, key=lambda r: (r["feature_category"], r["feature_type"], r["osm_id"]))

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore", restval="")
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

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download static geographic features from OpenStreetMap (Overpass API) for a city.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--city", required=True, help="City name (e.g. 'Delhi'). Used for logging, output folder naming, and bbox lookup.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory to write osm_features.csv and manifest.json into.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity.")
    return parser


def resolve_bbox_overpass(city: str) -> tuple[float, float, float, float]:
    """Returns (south, west, north, east) for the given city, reordered from CITY_BBOX_PRESETS' (min_lon,min_lat,max_lon,max_lat)."""
    bbox = CITY_BBOX_PRESETS.get(city.strip().lower())
    if bbox is None:
        raise OSMIngestError(
            f"No built-in bounding box for city '{city}'. "
            f"Known cities: {sorted(CITY_BBOX_PRESETS.keys())}."
        )
    min_lon, min_lat, max_lon, max_lat = bbox
    return (min_lat, min_lon, max_lat, max_lon)  # south, west, north, east


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        south, west, north, east = resolve_bbox_overpass(args.city)
    except OSMIngestError as exc:
        logger.error(str(exc))
        return 1

    city_slug = "".join(c if c.isalnum() else "_" for c in args.city.strip().lower())
    output_dir = args.output_dir / city_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    query = build_overpass_query(south, west, north, east)

    logger.info(
        "Starting OSM ingest: city=%s bbox(south,west,north,east)=(%.4f,%.4f,%.4f,%.4f) "
        "categories=%d output_dir=%s",
        args.city, south, west, north, east, len(FEATURE_SPECS), output_dir,
    )
    logger.debug("Overpass query:\n%s", query)

    session = build_session()

    try:
        payload = fetch_overpass_json(session, query)
    except OSMIngestError as exc:
        # Unlike the sibling modules, this module issues exactly one
        # request per city (per the frozen "avoid unnecessary repeated
        # calls" note) — there is no per-chunk continuation possible here,
        # so a failure at this stage is a total failure for the run.
        logger.error("Fatal error fetching OSM data for %s: %s", args.city, exc)
        return 1

    elements = payload.get("elements", [])
    logger.info("Received %d raw elements from Overpass for %s.", len(elements), args.city)

    rows, skipped = parse_elements(elements, args.city)

    if not rows:
        logger.error(
            "Zero usable features collected for city=%s. "
            "This is treated as a failure, not an empty success — check the bounding box and feature tags.",
            args.city,
        )
        manifest = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "OpenStreetMap via Overpass API",
            "overpass_endpoint": OVERPASS_ENDPOINT,
            "city": args.city,
            "bbox_south_west_north_east": [south, west, north, east],
            "categories_requested": sorted({spec.category for spec in FEATURE_SPECS}),
            "objects_downloaded": 0,
            "objects_skipped": skipped,
            "output_file": None,
            "status": "failed_zero_rows",
        }
        write_manifest(output_dir, manifest)
        return 1

    output_path = write_features_csv(output_dir, rows)

    category_counts: dict[str, int] = {}
    for row in rows:
        category_counts[row["feature_category"]] = category_counts.get(row["feature_category"], 0) + 1

    # This module makes exactly one request (no chunks to partially fail),
    # so "partial_success" here means something distinct from the chunked
    # sibling modules: it flags a run that produced usable data but also
    # had to skip some elements for missing/invalid geometry, so an
    # imperfect-but-usable run stays visibly different from a clean one.
    status = "success" if skipped == 0 else "partial_success"

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "OpenStreetMap via Overpass API",
        "overpass_endpoint": OVERPASS_ENDPOINT,
        "city": args.city,
        "bbox_south_west_north_east": [south, west, north, east],
        "categories_requested": sorted({spec.category for spec in FEATURE_SPECS}),
        "objects_downloaded": len(rows),
        "objects_skipped": skipped,
        "objects_by_category": category_counts,
        "output_file": str(output_path),
        "status": status,
    }
    manifest_path = write_manifest(output_dir, manifest)
    logger.info("Wrote manifest -> %s", manifest_path)

    logger.info(
        "Ingest complete: %d features written, %d skipped, output_dir=%s",
        len(rows), skipped, output_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
