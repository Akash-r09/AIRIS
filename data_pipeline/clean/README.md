# data_pipeline/clean/

Cleaning and alignment scripts, one per source, matching `ingest/`. De-duplicates, handles nulls/invalid values, and resamples to a consistent time step.

Each script asserts a minimum row count and non-null critical columns before writing output.
