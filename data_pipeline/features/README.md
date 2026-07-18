# data_pipeline/features/

`build_features.py` joins all cleaned sources into a single model-ready table: lag features, weather features, fire-count features, land-use category, and calendar flags.

Output: `data_pipeline/processed/features.parquet` — one row per (cell_id, timestamp).
