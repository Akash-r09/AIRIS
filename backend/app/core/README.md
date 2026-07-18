# backend/app/core/

Cross-cutting infrastructure: configuration loading (`config.py`) and logging setup (`logging.py`).

Nothing here is business logic. Every other module in `backend/app/` may import from `core/`, but `core/` never imports from `services/`, `api/`, or `ml/`.
