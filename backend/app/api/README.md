# backend/app/api/

FastAPI route handlers. Each file owns exactly one resource (forecast, attribution, recommendation, casestudy).

Rules for this folder:
- No pandas/numpy imports here.
- No business logic — a route parses the request, calls exactly one service function, and returns the response.
- No pydantic model definitions here — import from `backend/app/models/schemas.py`.
