"""Application configuration loaded from the environment."""

import base64
import binascii
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

    # Match any `*.vercel.app` origin in addition to `cors_origins`. Preview
    # deployments get a new hostname on every push, so listing them one by one
    # is not workable; this covers the production alias and every preview.
    cors_origin_regex: str = r"https://[a-zA-Z0-9-]+(\.vercel\.app)$"

    # Seed the demo users and posts on first boot when the database is empty.
    # Intended for a demo deploy; leave off for anything with real data.
    auto_seed: bool = False

    # Uploads are written here and served back through an authenticated route.
    # On a host with a persistent disk, point this at the mount (e.g. /data/uploads).
    upload_dir: str = "./uploads"
    max_upload_mb: int = 32

    # Password reset. The token is mailed as a link to `frontend_base_url`.
    # Without SMTP configured the link is logged and, in a non-production
    # setting, returned to the caller so the flow can be exercised.
    frontend_base_url: str = "http://localhost:12000"
    password_reset_ttl_minutes: int = 30
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@pulse.local"
    smtp_starttls: bool = True
    # Set true in production so a reset link is never handed back in the API
    # response, whatever the mail configuration.
    expose_reset_token: bool = False

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host)

    @property
    def may_return_reset_token(self) -> bool:
        """Whether the reset token may be echoed in the response body."""
        return not self.mail_configured and not self.expose_reset_token

    # Request-body ceilings, in bytes. Uploads are allowed the full
    # `max_upload_mb`, while ordinary JSON payloads stay small.
    max_body_bytes: int = 1 * 1024 * 1024

    # Web Push (VAPID). The public key is served to the browser so it can
    # subscribe; the private key signs the pushes. Generate a pair once with
    # `python -m app.push keys` and set both in the environment — a pair that
    # changes invalidates every subscription already stored, so treat them as
    # deployment secrets rather than per-boot values.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    # The `sub` claim. Push services require a mailto: or https: contact so they
    # can reach an operator about a misbehaving sender.
    vapid_subject: str = "mailto:no-reply@pulse.local"
    # How long a push service should retain an undelivered alert, in seconds.
    push_ttl_seconds: int = 12 * 60 * 60

    @property
    def push_configured(self) -> bool:
        """Whether a usable VAPID key pair is present, so push can be offered.

        A public key that is not the 65-byte uncompressed point is treated as
        absent rather than served: a browser rejects it with an
        ``InvalidAccessError``, which surfaces to the user as a toggle that
        flips on and immediately fails. Reporting push as unavailable instead
        keeps the failure at configuration time, where it can be seen.
        """
        return bool(self.vapid_private_key) and self.valid_vapid_public_key is not None

    @property
    def valid_vapid_public_key(self) -> str | None:
        """The configured public key if a browser could subscribe with it."""
        if not self.vapid_public_key:
            return None
        try:
            raw = base64.urlsafe_b64decode(
                self.vapid_public_key + "=" * (-len(self.vapid_public_key) % 4)
            )
        except (binascii.Error, ValueError):
            return None
        # Uncompressed EC point on P-256: one 0x04 prefix byte plus 64 bytes.
        return self.vapid_public_key if len(raw) == 65 and raw[0] == 0x04 else None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    # Simple in-process rate limits (requests per window, window in seconds).
    rate_limit_requests: int = 240
    rate_limit_window_seconds: int = 60

    # Response caching for hot read endpoints. Redis is used when `redis_url`
    # is set and reachable; otherwise an in-process store is used, so the app
    # and its tests run fine without a Redis server.
    redis_url: str | None = "redis://localhost:6379/0"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 30
    cache_profile_ttl_seconds: int = 60
    cache_stats_ttl_seconds: int = 120

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cors_origin_pattern(self) -> str | None:
        """The regex CORS matches in addition to the explicit origin list."""
        return self.cors_origin_regex.strip() or None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
