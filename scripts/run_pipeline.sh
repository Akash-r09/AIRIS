#!/usr/bin/env bash
#
# scripts/run_pipeline.sh
#
# Runs the full AIRIS data pipeline in order:
#   ingest -> clean -> features
#
# Each stage is implemented in its own milestone (see docs/architecture.md,
# Part 3). This script is safe to run at any point in the sprint: a stage
# that hasn't been implemented yet is skipped with a clear message rather
# than failing the whole run, so this script itself needs no changes as
# later milestones land — only the underlying stage scripts do.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

info() { printf "\033[1;34m[pipeline]\033[0m %s\n" "$1"; }
warn() { printf "\033[1;33m[skip]\033[0m %s\n" "$1"; }
ok()   { printf "\033[1;32m[done]\033[0m %s\n" "$1"; }
fail() { printf "\033[1;31m[error]\033[0m %s\n" "$1" >&2; exit 1; }

if [ ! -x "$PYTHON_BIN" ]; then
  fail "Virtual environment not found. Run scripts/setup_env.sh first."
fi

# Runs a pipeline stage script if it exists, otherwise skips with a message.
# Usage: run_stage <human-readable label> <path/to/script.py>
run_stage() {
  local label="$1"
  local script_path="$2"

  if [ -f "$script_path" ]; then
    info "Running: $label ($script_path)"
    "$PYTHON_BIN" "$script_path"
    ok "$label"
  else
    warn "$label — $script_path not implemented yet (see relevant milestone in docs/architecture.md)."
  fi
}

echo "=================================================="
echo " AIRIS Data Pipeline"
echo "=================================================="

# ---- Stage 1: Ingest ----
# Pulls raw data from each external source into data_pipeline/raw/.
# Milestone 1 in the blueprint. Four independent, source-specific scripts.
info "Stage 1/3 — Ingest"
run_stage "Ingest: OpenAQ historical AQI"        "data_pipeline/ingest/openaq.py"
run_stage "Ingest: Open-Meteo historical weather" "data_pipeline/ingest/openmeteo.py"
run_stage "Ingest: NASA FIRMS fire hotspots"      "data_pipeline/ingest/firms.py"
run_stage "Ingest: OSM land use / road network"   "data_pipeline/ingest/osm.py"

# ---- Stage 2: Clean ----
# Cleans, deduplicates, and aligns each raw source. Milestone 2.
info "Stage 2/3 — Clean"
run_stage "Clean: AQI data"     "data_pipeline/clean/clean_aqi.py"
run_stage "Clean: Weather data" "data_pipeline/clean/clean_weather.py"
run_stage "Clean: Fire data"    "data_pipeline/clean/clean_fires.py"

# ---- Stage 3: Feature engineering ----
# Joins all cleaned sources into the single model-ready feature table
# consumed by both the forecast and attribution models. Milestone 3.
info "Stage 3/3 — Feature engineering"
run_stage "Build features.parquet" "data_pipeline/features/build_features.py"

echo "=================================================="
ok "Pipeline run complete."
echo "  Note: the reference dataset (data_pipeline/reference/dss_reference.csv)"
echo "  is manually curated, not part of this automated pipeline — see"
echo "  data_pipeline/reference/README.md."
echo "=================================================="
