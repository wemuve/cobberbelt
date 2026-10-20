"""Application configuration.

All settings come from environment variables (prefix ``CBI_``) or a ``.env`` file.
Secrets are never given defaults that would work in production: the app refuses
to start in ``production`` with the development JWT secret.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CBI_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"

    # Database: the owner URL runs migrations; the app URL is a non-superuser
    # role so PostgreSQL row-level security policies actually apply.
    database_url: str = "postgresql+psycopg://copperbelt_app:devpassword@localhost:5432/copperbelt"
    database_owner_url: str = "postgresql+psycopg://copperbelt:devpassword@localhost:5432/copperbelt"

    # Auth
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_issuer: str = "copperbelt-intelligence"
    access_token_minutes: int = 30
    refresh_token_days: int = 14

    # HTTP
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    rate_limit_per_minute: int = 120
    login_rate_limit_per_minute: int = 10

    # Object storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: Path = Path("./var/objects")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None

    # Uploads
    max_upload_bytes: int = 200 * 1024 * 1024
    clamav_host: str | None = None  # clamd host; scanning is reported as unavailable when unset
    clamav_port: int = 3310
    require_malware_scan: bool = False  # set true in production once clamd is deployed

    # OCR
    ocr_languages: str = "eng+fra"
    ocr_dpi: int = 300
    ocr_min_text_chars: int = 40  # pages with fewer text-layer chars are OCR'd

    # Jobs
    worker_poll_seconds: float = 1.0
    job_lock_timeout_seconds: int = 900

    # External providers (unset => reported as "unconfigured", never simulated)
    planet_api_key: SecretStr | None = None
    planet_base_url: str = "https://api.planet.com"
    planet_monthly_budget_usd: float | None = None
    pvgis_base_url: str = "https://re.jrc.ec.europa.eu/api/v5_3"
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    embedding_provider: Literal["none", "openai"] = "none"
    embedding_api_key: SecretStr | None = None
    embedding_model: str | None = None
    embedding_dimensions: int = 1536

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.environment in {"staging", "production"}:
            if self.jwt_secret.get_secret_value() == DEV_JWT_SECRET:
                raise ValueError("CBI_JWT_SECRET must be set outside development")
            if len(self.jwt_secret.get_secret_value()) < 32:
                raise ValueError("CBI_JWT_SECRET must be at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
