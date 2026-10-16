"""Rate limiting, body-size ceilings and security headers."""

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.middleware import (
    CONTENT_SECURITY_POLICY,
    SENSITIVE_LIMIT,
    RateLimitMiddleware,
)

from .test_media import PNG


@pytest.fixture
def limited_client():
    """A throwaway app so the process-wide limiter starts from a clean slate."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/auth/login")
    def login() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/posts")
    def posts() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        yield client


def test_sensitive_endpoint_returns_429_after_limit(limited_client):
    for _ in range(SENSITIVE_LIMIT):
        assert limited_client.post("/api/auth/login").status_code == 200

    blocked = limited_client.post("/api/auth/login")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]
    # A retry hint lets a well-behaved client back off instead of hammering.
    assert int(blocked.headers["Retry-After"]) >= 1


def test_rate_limit_is_scoped_per_endpoint_class(limited_client):
    """Exhausting the auth budget must not lock out ordinary reads."""
    for _ in range(SENSITIVE_LIMIT):
        limited_client.post("/api/auth/login")
    assert limited_client.post("/api/auth/login").status_code == 429

    assert limited_client.get("/api/posts").status_code == 200


def test_media_has_its_own_budget(limited_client):
    """A busy media budget must not stop ordinary reads, and vice versa.

    Regression guard: media and general reads shared one bucket, so a
    media-heavy feed — which is every feed — exhausted the allowance and then
    every picture and avatar was answered with 429. That looked like the app
    breaking rather than like a rate limit.
    """
    from app.middleware import MEDIA_LIMIT_MULTIPLIER

    app = limited_client.app

    @app.get("/api/media/{media_id}")
    def media(media_id: int) -> dict[str, bool]:
        return {"ok": True}

    media_budget = settings.rate_limit_requests * MEDIA_LIMIT_MULTIPLIER

    # Burn the whole general-read allowance.
    for _ in range(settings.rate_limit_requests):
        assert limited_client.get("/api/posts").status_code == 200
    assert limited_client.get("/api/posts").status_code == 429

    # Media still works, because it is counted separately.
    assert limited_client.get("/api/media/1").status_code == 200

    # And the media allowance is genuinely larger than the read one.
    assert media_budget > settings.rate_limit_requests


def test_media_upload_does_not_get_the_download_budget(limited_client):
    """A write must not inherit the generous read allowance.

    Uploads share the `/api/media` prefix, so a prefix-only check would hand
    them the 12x download ceiling — 2880 uploads a minute of up to 32MB each,
    which is a way to fill the instance's storage rather than to serve a feed.
    """
    from app.middleware import MEDIA_LIMIT_MULTIPLIER

    app = limited_client.app

    @app.post("/api/media")
    def upload() -> dict[str, bool]:
        return {"ok": True}

    for _ in range(settings.rate_limit_requests):
        assert limited_client.post("/api/media").status_code == 200
    # The upload budget is the ordinary one, not the media download one.
    assert limited_client.post("/api/media").status_code == 429
    assert MEDIA_LIMIT_MULTIPLIER > 1


def test_limiter_counts_per_client(limited_client):
    """One noisy caller must not consume another caller's budget."""
    for _ in range(SENSITIVE_LIMIT):
        limited_client.post("/api/auth/login", headers={"x-forwarded-for": "10.0.0.1"})
    assert (
        limited_client.post("/api/auth/login", headers={"x-forwarded-for": "10.0.0.1"}).status_code
        == 429
    )
    assert (
        limited_client.post("/api/auth/login", headers={"x-forwarded-for": "10.0.0.2"}).status_code
        == 200
    )


def test_security_headers_on_api_response(client):
    headers = client.get("/api/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_hsts_only_when_request_arrived_over_tls(client):
    """HSTS is meaningless on plain http, so it is omitted there."""
    assert "Strict-Transport-Security" not in client.get("/api/health").headers

    proxied = client.get("/api/health", headers={"x-forwarded-proto": "https"})
    assert proxied.headers["Strict-Transport-Security"].startswith("max-age=")


def test_oversized_json_body_is_rejected(client):
    oversized = "x" * (settings.max_body_bytes + 1024)
    response = client.post("/api/posts", json={"body": oversized})
    assert response.status_code == 413
    assert response.json()["detail"]


def test_normal_sized_body_is_not_rejected(client):
    response = client.post("/api/posts", json={"body": "hello"})
    # Rejected for missing auth, not for size.
    assert response.status_code == 401


def test_avatar_upload_gets_the_upload_ceiling(client, make_user, tmp_path, monkeypatch):
    """An avatar between the JSON and upload ceilings must reach the handler.

    Regression guard: profile picture and cover routes were left on the small
    JSON body limit, so a picture comfortably under the 8MB image cap was
    rejected at the door with 413 before the route could accept it.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    # Larger than max_body_bytes, smaller than the image limit.
    payload = PNG + b"\x00" * (settings.max_body_bytes + 1024)
    assert len(payload) < settings.max_upload_bytes

    response = client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"file": ("big.png", io.BytesIO(payload), "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["avatar_url"]


def test_cover_upload_gets_the_upload_ceiling(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    payload = PNG + b"\x00" * (settings.max_body_bytes + 1024)
    response = client.post(
        "/api/users/me/cover",
        headers=headers,
        files={"file": ("big.png", io.BytesIO(payload), "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["cover_url"]


def test_json_endpoint_keeps_the_small_ceiling(client):
    """Widening the upload routes must not widen ordinary JSON payloads."""
    oversized = "x" * (settings.max_body_bytes + 1024)
    assert client.post("/api/posts", json={"body": oversized}).status_code == 413
