# backend/app/data/

Read-only data access layer. Reads pre-processed parquet/sqlite tables written by `data_pipeline/`.

This folder never issues an outbound network request. If a table is missing, it fails loudly rather than silently returning empty data.
