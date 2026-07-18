# backend/app/

The FastAPI application package. Organized by responsibility:

- `api/` — HTTP route handlers only, no business logic
- `core/` — configuration, logging, shared constants
- `models/` — Pydantic schemas (the API contract)
- `services/` — business logic (forecasting, attribution, priority, briefing)
- `data/` — read-only data access layer over processed tables
- `main.py` — FastAPI app instantiation

Dependency direction is strictly one-way: `api -> services -> data/ml`. Never the reverse.
