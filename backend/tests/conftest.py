"""Shared pytest fixtures. Each test gets an isolated in-memory database."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.middleware import RateLimitMiddleware  # noqa: E402


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
