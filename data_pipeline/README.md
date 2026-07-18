# data_pipeline/

Fully separate from `backend/`. Produces the versioned, pre-computed tables the backend reads at request time.

Stages run in order: `ingest/` -> `clean/` -> `features/`. `reference/` is a parallel, manually curated dataset (not automated). `raw/` and `processed/` hold pipeline outputs and are gitignored.
