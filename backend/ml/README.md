# backend/ml/

Model training and inference code, separate from `app/services/` because training (offline, run once) and inference (online, run per request) have different lifecycles.

- `forecast/` — LightGBM/XGBoost AQI forecasting model
- `attribution/` — source attribution scoring engine (rules + explainability)
- `artifacts/` — trained model files, gitignored and regenerable via `scripts/train_models.sh`
