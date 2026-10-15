"""Cross-cutting HTTP middleware: rate limiting, body-size limits, security headers."""

import time
from collections import defaultdict, deque

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings

# Endpoints that mint credentials get a much tighter budget than general reads.
SENSITIVE_PATHS = {"/api/auth/login", "/api/auth/register"}
SENSITIVE_LIMIT = 10

# The SPA is served from the same origin as the API, so it needs no external
# script, style or font origins. `data:` is allowed for inline SVG and blobs for
# locally previewed uploads; websockets are needed for realtime.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        # Vite injects inline style tags during development.
        "style-src 'self' 'unsafe-inline'",
        "script-src 'self'",
        "connect-src 'self' ws: wss:",
    )
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A fixed-window counter keyed by client IP.

    In-process and therefore per-worker; it exists to blunt credential stuffing
    and accidental hot loops in a single-node deployment. A multi-node setup
    should move this counter into Redis.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        # Honour the proxy header the runtime sets, falling back to the socket.
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            limit = SENSITIVE_LIMIT if path in SENSITIVE_PATHS else settings.rate_limit_requests
            window = settings.rate_limit_window_seconds
            key = f"{self._client_key(request)}:{'auth' if path in SENSITIVE_PATHS else 'api'}"
            now = time.monotonic()

            bucket = self._hits[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window - (now - bucket[0])))
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Too many requests. Please slow down."},
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies and stamp hardening headers on every response.

    Kept separate from the rate limiter so the limiter can be disabled in tests
    without silently dropping these guarantees.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Reject oversized bodies before the framework buffers them into memory.
        # Uploads get a larger budget than JSON payloads.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            ceiling = (
                settings.max_upload_bytes
                if path.startswith("/api/media")
                else settings.max_body_bytes
            )
            if int(declared) > ceiling:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Request body is too large."},
                )

        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        # Only meaningful over TLS, so it is skipped on plain-http development.
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
