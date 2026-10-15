"""Rate limiting, body-size ceilings and security headers."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.middleware import (
    CONTENT_SECURITY_POLICY,
    SENSITIVE_LIMIT,
    RateLimitMiddleware,
)


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
