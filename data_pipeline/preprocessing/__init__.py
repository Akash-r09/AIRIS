"""
data_pipeline/preprocessing/

The cleaning/merge stage of the AIRIS pipeline: converts the raw CSV
outputs of data_pipeline/ingest/{openaq,openmeteo,firms,osm}.py into one
clean, chronologically-sorted, per-city dataset ready for feature
engineering. No ML, no feature engineering, no forecasting happens here —
only cleaning, validation, merging, and alignment.

- validators.py -- shared exception, required-column schemas, timestamp/
  numeric validation helpers. No dependency on clean.py or merge.py.
- clean.py -- per-source loading, validation, and reshaping
  (load_and_clean_openaq / _openmeteo / _firms / _osm).
- merge.py -- orchestrates clean.py, merges all sources into one dataset,
  writes clean_dataset.csv and manifest.json. Also the CLI entrypoint:
  `python data_pipeline/preprocessing/merge.py --city Delhi`.
"""

from .merge import main, run_preprocessing

__all__ = ["main", "run_preprocessing"]
