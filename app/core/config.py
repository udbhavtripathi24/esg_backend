"""Application configuration.

Cloud-neutral by design (decision #1): domain/business logic never imports
GCP SDKs directly. Infrastructure adapters (storage, events) will read these
settings and pick an implementation. Locally we default to Postgres via
docker-compose; in production these come from the environment / Secret Manager.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core ---
    ENVIRONMENT: str = "development"  # development | staging | production
    PROJECT_NAME: str = "Deloitte ESG Platform API"
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    # Local dev via docker-compose defaults to this Postgres. Never SQLite for
    # the real schema (JSONB, composite FKs, etc. are Postgres features we rely on).
    DATABASE_URL: str = "postgresql+psycopg2://esg:esg@localhost:5432/esg_platform"

    # --- Auth ---
    SECRET_KEY: str = "dev-secret-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:5175"

    # --- Cloud abstraction seams (unused in Stage 1, present so later stages
    #     configure GCP without touching domain code). Empty = local/no-op. ---
    STORAGE_BACKEND: str = "local"     # local | gcs
    STORAGE_BUCKET: str = ""
    STORAGE_LOCAL_ROOT: str = "./storage"
    EVENT_BACKEND: str = "noop"        # noop | pubsub
    GCP_PROJECT_ID: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
