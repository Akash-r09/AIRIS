# AIRIS — Artificial Intelligence for Real-time Intervention Strategy

AIRIS is an AI Chief Environmental Officer for urban air quality. Instead of showing an administrator
that the AQI is high, it explains *why* it's high, *who* is likely responsible, *what* intervention
should be taken, and *where* inspectors should be deployed first — with a confidence score attached
to every claim.

Built for the ET AI Hackathon (Problem 5: AI-Powered Urban Air Quality Intelligence for Smart City
Intervention) as a 3-day prototype by a 2–3 person undergraduate team.

## Features

1. **Hyperlocal AQI Forecast** — 24–48 hour AQI forecasts at ward/grid-cell resolution, with
   prediction intervals, not just point estimates.
2. **Ground-Truth-Calibrated Source Attribution** — an explainable engine that attributes pollution
   to source categories (stubble burning, vehicular, construction, etc.) with a confidence score,
   benchmarked against a manually curated set of publicly reported SAFAR/DSS figures for documented
   dates.
3. **Enforcement Priority Ranking** — deterministic, explainable scoring that turns attribution +
   forecast severity into a ranked, justified list of recommended interventions.
4. **Officer Briefing Generator** — a single scoped LLM call that narrates the already-computed
   numbers above into a plain-language briefing paragraph. The LLM never invents a number.
5. **Decision Console** — a single-screen map + briefing panel UI (no multi-tab dashboard), demoed
   end-to-end against one real historical event: the Delhi Diwali 2024 AQI spike.

## Architecture

```
Data Sources (OpenAQ + Open-Meteo + NASA FIRMS + OSM)
        │
        ▼
AQI Forecast Engine (LightGBM / XGBoost)
        │
        ▼
Evidence Engine (Rules + Explainability + Confidence)
        │
        ▼
Priority Engine (Deterministic Scoring)
        │
        ▼
Officer Brief Generator (Gemini API)
        │
        ▼
Decision Console (React + Leaflet)
```

The backend never fetches external data at request time — all model inputs are pre-computed by
`data_pipeline/` and read from disk. This keeps the live demo fast and immune to any external API
being slow or unavailable on stage. Full detail in `docs/architecture.md`.

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI |
| ML | scikit-learn, LightGBM, XGBoost, SHAP |
| Geospatial | GeoPandas, OSMnx |
| Data storage | Parquet (pandas/pyarrow), DuckDB/SQLite |
| Frontend | React 18, TypeScript, Vite, Leaflet, TailwindCSS |
| LLM | Gemini API |
| Data sources | OpenAQ, Open-Meteo, NASA FIRMS, OpenStreetMap |

Every choice above is CPU-only and validated to run comfortably on a MacBook Air M1 — no GPU,
no deep learning, no distributed compute. See `docs/architecture.md` Part 5 for the full validation.

## Repository Structure

```
airis/
├── backend/          FastAPI service, business logic, ML training/inference
├── data_pipeline/     Ingestion, cleaning, feature engineering (runs offline, writes to disk)
├── frontend/          React + Leaflet decision console
├── configs/           Non-secret settings + .env template
├── scripts/           setup / pipeline / training entrypoints
├── docs/              Architecture, demo script, data source notes
└── notebooks/         Exploratory analysis only — never imported by app code
```

Every folder contains its own `README.md` explaining its specific responsibility.

## Setup Instructions

Prerequisites: Python 3.11+, Node 20 LTS.

```bash
# 1. Clone and enter the repo
git clone <repo-url> airis && cd airis

# 2. Copy the environment template and fill in real values
cp .env.example .env
# Edit .env: add GEMINI_API_KEY, EARTHDATA_LOGIN/EARTHDATA_PASSWORD

# 3. Bootstrap everything (creates venv, installs backend + frontend deps)
bash scripts/setup_env.sh

# 4. Run the data pipeline (once data source scripts are implemented)
bash scripts/run_pipeline.sh

# 5. Train models (once training scripts are implemented)
bash scripts/train_models.sh

# 6. Start the backend
source .venv/bin/activate
uvicorn backend.app.main:app --reload --port 8000

# 7. Start the frontend (separate terminal)
cd frontend && npm run dev
```

At the end of Milestone 0, only steps 1–3 and 6 are meaningful — the app boots with a health
endpoint and no business logic yet. Steps 4–5 become real starting at their respective milestones.

## Development Workflow

- Follow `docs/architecture.md` — the engineering blueprint is frozen; no new features, no new
  libraries, no folder restructuring outside of fixing an actual bug.
- Work milestone by milestone (see the blueprint's Part 3 table). Each milestone should leave the
  repo in a runnable, inspectable state before the next one starts.
- Commit format: `<type>(<scope>): <summary>` — e.g. `feat(forecast): add lag feature engineering`.
- No secrets are ever committed. `.env` is gitignored; `.env.example` documents required keys with
  placeholders only.
- Data pipeline outputs (`data_pipeline/raw/`, `data_pipeline/processed/`) and model artifacts
  (`backend/ml/artifacts/`) are gitignored — they are regenerable, not source-controlled.
