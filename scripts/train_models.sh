#!/usr/bin/env bash
#
# scripts/train_models.sh
#
# Trains (or retrains) the AIRIS models in order:
#   forecast model -> attribution model -> attribution validation
#
# Requires data_pipeline/processed/features.parquet to already exist —
# run scripts/run_pipeline.sh first. Like run_pipeline.sh, each stage is
# skipped with a clear message if not yet implemented, so this script
# doesn't need edits as later milestones land.
#
# Model artifacts are versioned by filename (see configs/settings.yaml,
# models.forecast.active_version / models.attribution.active_version) and
# are never silently overwritten — retraining produces a new version.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
FEATURES_FILE="$REPO_ROOT/data_pipeline/processed/features.parquet"

info() { printf "\033[1;34m[train]\033[0m %s\n" "$1"; }
warn() { printf "\033[1;33m[skip]\033[0m %s\n" "$1"; }
ok()   { printf "\033[1;32m[done]\033[0m %s\n" "$1"; }
fail() { printf "\033[1;31m[error]\033[0m %s\n" "$1" >&2; exit 1; }

if [ ! -x "$PYTHON_BIN" ]; then
  fail "Virtual environment not found. Run scripts/setup_env.sh first."
fi

if [ ! -f "$FEATURES_FILE" ]; then
  warn "features.parquet not found at $FEATURES_FILE — run scripts/run_pipeline.sh first."
  warn "Continuing anyway; individual training stages below will skip if their script isn't implemented."
fi

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
echo " AIRIS Model Training"
echo "=================================================="

# ---- Stage 1: Forecast model ----
# Trains LightGBM/XGBoost on features.parquet, saves a versioned artifact
# to backend/ml/artifacts/. Milestone 5.
info "Stage 1/3 — Forecast model"
run_stage "Train forecast model" "backend/ml/forecast/train.py"

# ---- Stage 2: Attribution engine ----
# Builds the rules/classifier-based source attribution scorer. Milestone 6.
info "Stage 2/3 — Attribution engine"
run_stage "Build attribution scorer" "backend/ml/attribution/scorer.py"

# ---- Stage 3: Attribution validation ----
# Compares attribution output against the manually curated reference set
# for matching dates. Milestone 6.
info "Stage 3/3 — Attribution validation"
run_stage "Validate attribution against reference" "backend/ml/attribution/validate_against_reference.py"

echo "=================================================="
ok "Training run complete."
echo "=================================================="
