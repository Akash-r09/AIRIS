# backend/app/services/

Business logic layer. Each service wraps one concern:

- `forecast_service.py` — calls `backend/ml/forecast/predict.py`
- `attribution_service.py` — calls `backend/ml/attribution/scorer.py`
- `priority_service.py` — deterministic scoring, no I/O
- `briefing_service.py` — single scoped Gemini API call, narrates already-computed numbers only

Services are the only layer allowed to import from both `ml/` and `data/`.
