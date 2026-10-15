"""Application configuration loaded from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Pulse API"
    database_url: str = "sqlite:///./pulse.db"

    # In production this must be provided through the environment.
    secret_key: str = "dev-secret-key-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    # Comma separated list of origins allowed to call the API.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Uploads are written here and served back through an authenticated route.
    upload_dir: str = "./uploads"
    max_upload_mb: int = 32

    # Request-body ceilings, in bytes. Uploads are allowed the full
    # `max_upload_mb`, while ordinary JSON payloads stay small.
    max_body_bytes: int = 1 * 1024 * 1024

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    # Simple in-process rate limits (requests per window, window in seconds).
    rate_limit_requests: int = 240
    rate_limit_window_seconds: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
