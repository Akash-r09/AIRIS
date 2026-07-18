# backend/ml/attribution/

Source attribution scoring engine.

- `scorer.py` — rules engine plus, where labels exist, a lightweight classifier; produces ranked source shares with confidence and SHAP-based feature contributions
- `validate_against_reference.py` — one-off script comparing model output to the manually curated `data_pipeline/reference/dss_reference.csv` on matching dates

This is intentionally interpretable rather than a black box — interpretability is a requirement here, not a nice-to-have.
