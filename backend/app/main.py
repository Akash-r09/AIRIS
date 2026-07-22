"""
AIRIS backend entrypoint.

App instantiation, CORS, a health check, and business route mount points.
The forecast router (backend/app/api/routes_forecast.py) is now mounted.
Remaining routers (attribution, recommendation, casestudy) land in
Milestone 10, each mounted here with its own `include_router` call.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(level=settings.env.log_level)

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Application factory — keeps app construction testable and import-safe."""
    app = FastAPI(
        title=settings.yaml.app.name,
        version=settings.yaml.app.version,
        description="AI Chief Environmental Officer for urban air quality intelligence.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.env.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_routes(app)

    return app


def register_routes(app: FastAPI) -> None:
    """
    Mounts all API routers. Milestone 0: no routers exist yet, so this is
    intentionally empty beyond the health check below. Each future route
    module (backend/app/api/routes_forecast.py, routes_attribution.py,
    routes_recommendation.py, routes_casestudy.py) adds exactly one
    `app.include_router(...)` call here — this function is the only place
    routers get mounted.
    """
    api_prefix = settings.yaml.api.prefix

    from backend.app.api.routes_forecast import router as forecast_router
    app.include_router(forecast_router, prefix=api_prefix, tags=["forecast"])

    from backend.app.api.routes_dashboard import router as dashboard_router
    app.include_router(dashboard_router, prefix=api_prefix, tags=["dashboard"])

    # --- Placeholder mount points (Milestone 10) ---
    # from backend.app.api.routes_attribution import router as attribution_router
    # app.include_router(attribution_router, prefix=api_prefix, tags=["attribution"])
    #
    # from backend.app.api.routes_recommendation import router as recommendation_router
    # app.include_router(recommendation_router, prefix=api_prefix, tags=["recommendation"])
    #
    # from backend.app.api.routes_casestudy import router as casestudy_router
    # app.include_router(casestudy_router, prefix=api_prefix, tags=["casestudy"])

    logger.info("Routers registered under prefix '%s'.", api_prefix)


app = create_app()


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Liveness/readiness check. Returns app name, version, and environment."""
    return {
        "status": "ok",
        "app": settings.yaml.app.name,
        "version": settings.yaml.app.version,
        "environment": settings.env.app_env,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=settings.env.backend_host,
        port=settings.env.backend_port,
        reload=True,
    )
