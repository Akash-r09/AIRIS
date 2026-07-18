# backend/ml/forecast/

Hyperlocal AQI forecasting model.

- `train.py` — trains LightGBM/XGBoost on `data_pipeline/processed/features.parquet`, saves a versioned artifact
- `predict.py` — loads an artifact, returns a point forecast plus a prediction interval

Artifacts are versioned by filename (e.g. `forecast_v1.pkl`); retraining never silently overwrites the version pinned for a demo.
