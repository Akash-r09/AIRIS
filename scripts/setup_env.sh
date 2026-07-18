#!/usr/bin/env bash
#
# scripts/setup_env.sh
#
# Bootstraps the full AIRIS development environment:
#   1. Verifies Python and Node versions meet minimum requirements.
#   2. Creates (or reuses) a Python virtual environment at .venv/.
#   3. Installs backend dependencies from requirements.txt.
#   4. Installs frontend dependencies via npm.
#
# Idempotent: safe to re-run at any point in the sprint. Existing venv and
# node_modules are reused rather than recreated from scratch.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11
MIN_NODE_MAJOR=20

VENV_DIR="$REPO_ROOT/.venv"
FRONTEND_DIR="$REPO_ROOT/frontend"

info()  { printf "\033[1;34m[setup]\033[0m %s\n" "$1"; }
ok()    { printf "\033[1;32m[ok]\033[0m %s\n" "$1"; }
fail()  { printf "\033[1;31m[error]\033[0m %s\n" "$1" >&2; exit 1; }

# ---- 1. Verify Python version ----
info "Checking Python version..."
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 not found. Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ before continuing."
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MAJOR="$(echo "$PYTHON_VERSION" | cut -d. -f1)"
PYTHON_MINOR="$(echo "$PYTHON_VERSION" | cut -d. -f2)"

if [ "$PYTHON_MAJOR" -lt "$MIN_PYTHON_MAJOR" ] || { [ "$PYTHON_MAJOR" -eq "$MIN_PYTHON_MAJOR" ] && [ "$PYTHON_MINOR" -lt "$MIN_PYTHON_MINOR" ]; }; then
  fail "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ required, found ${PYTHON_VERSION}."
fi
ok "Python ${PYTHON_VERSION} detected."

# ---- 2. Verify Node version ----
info "Checking Node version..."
if ! command -v node >/dev/null 2>&1; then
  fail "node not found. Install Node ${MIN_NODE_MAJOR} LTS before continuing."
fi

NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
if [ "$NODE_MAJOR" -lt "$MIN_NODE_MAJOR" ]; then
  fail "Node ${MIN_NODE_MAJOR}+ required, found $(node -v)."
fi
ok "Node $(node -v) detected."

# ---- 3. Create / reuse Python virtual environment ----
if [ -d "$VENV_DIR" ]; then
  info "Virtual environment already exists at .venv/ — reusing it."
else
  info "Creating virtual environment at .venv/..."
  python3 -m venv "$VENV_DIR"
  ok "Virtual environment created."
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ---- 4. Install backend dependencies ----
info "Upgrading pip..."
pip install --upgrade pip --quiet

info "Installing backend dependencies from requirements.txt..."
pip install -r "$REPO_ROOT/requirements.txt" --quiet
ok "Backend dependencies installed."

deactivate

# ---- 5. Install frontend dependencies ----
if [ -d "$FRONTEND_DIR" ]; then
  info "Installing frontend dependencies..."
  (cd "$FRONTEND_DIR" && npm install --silent)
  ok "Frontend dependencies installed."
else
  fail "frontend/ directory not found at $FRONTEND_DIR."
fi

# ---- 6. Check for .env ----
if [ ! -f "$REPO_ROOT/.env" ]; then
  info "No .env file found. Copying .env.example -> .env (fill in real values before running the app)."
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
fi

echo ""
ok "AIRIS environment setup complete."
echo "  Activate the backend venv with:  source .venv/bin/activate"
echo "  Start the backend with:          uvicorn backend.app.main:app --reload --port 8000"
echo "  Start the frontend with:         cd frontend && npm run dev"
