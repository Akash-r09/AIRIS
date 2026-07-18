"""
Configuration loading for AIRIS.

Combines two sources into a single typed object:
  - configs/settings.yaml  -> non-secret configuration (city, paths, model
    versions, API config). Parsed and validated into `YamlConfig`.
  - .env / process environment -> secrets and deployment-specific values
    (API keys, host/port, CORS origins). Parsed via pydantic-settings into
    `EnvSettings`.

`get_settings()` is the single entrypoint every other module should use.
It is cached (`lru_cache`) so the YAML file is parsed once per process,
not on every request.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root, resolved relative to this file's location
# (backend/app/core/config.py -> repo root is three levels up).
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SETTINGS_YAML_PATH = REPO_ROOT / "configs" / "settings.yaml"
DEFAULT_ENV_FILE_PATH = REPO_ROOT / ".env"


# ---------------------------------------------------------------------------
# YAML-derived configuration (non-secret)
# ---------------------------------------------------------------------------

class AppInfo(BaseModel):
    name: str
    version: str


class CityConfig(BaseModel):
    name: str
    bbox: list[float] = Field(min_length=4, max_length=4)
    timezone: str


class UpwindRegionConfig(BaseModel):
    name: str
    bbox: list[float] = Field(min_length=4, max_length=4)


class CaseStudyConfig(BaseModel):
    name: str
    start_date: str
    end_date: str
    peak_date: str


class DataPipelineConfig(BaseModel):
    historical_start_date: str
    historical_end_date: str
    time_step: str


class PathsConfig(BaseModel):
    # protected_namespaces=() disables pydantic's "model_" prefix warning —
    # model_artifacts_dir is a legitimate field name matching settings.yaml,
    # not an accidental collision with pydantic's own model_* internals.
    model_config = {"protected_namespaces": ()}

    raw_data_dir: str
    processed_data_dir: str
    features_file: str
    reference_csv: str
    model_artifacts_dir: str


class ModelVersionConfig(BaseModel):
    active_version: str
    filename_template: str


class ModelsConfig(BaseModel):
    forecast: ModelVersionConfig
    attribution: ModelVersionConfig


class ApiYamlConfig(BaseModel):
    prefix: str
    request_timeout_seconds: int
    default_forecast_horizon_hours: int


class ForecastYamlConfig(BaseModel):
    quantiles: list[float]
    lag_hours: list[int]


class YamlConfig(BaseModel):
    """Root model for configs/settings.yaml."""

    app: AppInfo
    city: CityConfig
    upwind_region: UpwindRegionConfig
    case_study: CaseStudyConfig
    data_pipeline: DataPipelineConfig
    paths: PathsConfig
    models: ModelsConfig
    api: ApiYamlConfig
    forecast: ForecastYamlConfig


def _load_yaml_config(path: Path = DEFAULT_SETTINGS_YAML_PATH) -> YamlConfig:
    if not path.is_file():
        raise FileNotFoundError(
            f"settings.yaml not found at {path}. "
            "AIRIS requires configs/settings.yaml to boot — see docs/architecture.md."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"settings.yaml at {path} is empty or invalid.")

    return YamlConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Environment-derived configuration (secrets + deployment values)
# ---------------------------------------------------------------------------

class EnvSettings(BaseSettings):
    """Values sourced from .env / process environment. See .env.example."""

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App environment
    app_env: str = "development"
    log_level: str = "INFO"

    # Backend server
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Frontend
    frontend_api_base_url: str = "http://localhost:8000"

    # LLM
    gemini_api_key: str = ""

    # NASA FIRMS
    earthdata_login: str = ""
    earthdata_password: str = ""
    firms_map_key: str = ""

    # OpenAQ
    openaq_api_key: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


# ---------------------------------------------------------------------------
# Combined settings object — the single entrypoint for the rest of the app
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    """Unified, typed configuration combining YAML config and environment."""

    env: EnvSettings
    yaml: YamlConfig

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a path from settings.yaml (e.g. paths.features_file) against REPO_ROOT."""
        return REPO_ROOT / relative_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns the process-wide Settings singleton, loading and validating
    configs/settings.yaml and the environment exactly once.
    """
    return Settings(env=EnvSettings(), yaml=_load_yaml_config())
