"""Shared pytest fixtures. Each test gets an isolated in-memory database."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.cache import reset_store  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.middleware import RateLimitMiddleware  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture
def anyio_backend():
    """Run `pytest.mark.anyio` tests on asyncio only (not trio)."""
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    """Give every test its own cache store.

    The cache backend is process-global, so without this a response cached by
    one test would be served to the next. The Redis URL is cleared so a server
    running on the developer's machine cannot leak state between tests.

    The last-seen throttle is reset for the same reason: it is keyed by user id
    and lives for the life of the process, so a user stamped by an earlier test
    would suppress the write in a later one and leave `last_seen_at` null.
    """
    from app import deps

    monkeypatch.setattr(settings, "redis_url", None)
    reset_store()
    deps.reset_last_seen_tracking()
    yield
    reset_store()
    deps.reset_last_seen_tracking()


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # The rate limiter is process-wide, so a shared instance would leak state
    # between tests. Disable it here and cover it directly in test_media.py.
    middleware = [m for m in app.user_middleware if m.cls is not RateLimitMiddleware]
    app.user_middleware = middleware

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def make_user(client):
    """Register a user and return (headers, user_dict)."""

    def _make(username: str, password: str = "password123", **extra):
        payload = {
            "username": username,
            "email": f"{username}@example.com",
            "display_name": extra.pop("display_name", username.title()),
            "password": password,
            **extra,
        }
        response = client.post("/api/auth/register", json=payload)
        assert response.status_code == 201, response.text
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/auth/me", headers=headers).json()
        return headers, me

    return _make


@pytest.fixture
def make_admin(client, make_user):
    """Register a user and promote them, returning (headers, user_dict).

    There is no API route that grants admin (deliberately — it would be a
    privilege-escalation path), so the flag is set directly in the database.
    """
    from app.database import get_db

    def _make(username: str = "root", **extra):
        headers, me = make_user(username, **extra)
        override = client.app.dependency_overrides[get_db]
        session = next(override())
        try:
            user = session.get(User, me["id"])
            user.is_admin = True
            session.commit()
        finally:
            session.close()
        refreshed = client.get("/api/auth/me", headers=headers).json()
        assert refreshed["is_admin"] is True
        return headers, refreshed

    return _make
