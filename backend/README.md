# backend/

Contains the AIRIS FastAPI service: the API layer, business logic (services), and ML training/inference code.

This folder never fetches data from the internet at request time. It only reads pre-computed, versioned tables produced by `data_pipeline/`. This keeps the live API fast and demo-safe regardless of external API availability.
