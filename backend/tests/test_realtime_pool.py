"""Regression test: an idle realtime socket must not hold a pooled DB connection.

The websocket endpoint used to take its session from a request dependency, so
the session lived as long as the socket. A handful of idle sockets then pinned
every connection in the pool and each HTTP request blocked on pool_timeout
(~30s), which surfaced as the whole app hanging on its loading spinner.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.database import Base, get_db
from app.main import app
from app.middleware import RateLimitMiddleware

POOL_SIZE = 2
MAX_OVERFLOW = 0
# Fail fast instead of waiting the 30s production default if the pool is pinned.
POOL_TIMEOUT = 2


@pytest.fixture
def small_pool_client(tmp_path):
    """A client whose engine exposes only `POOL_SIZE` connections."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
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

    app.user_middleware = [m for m in app.user_middleware if m.cls is not RateLimitMiddleware]
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.engine = engine
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _register(client, username):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username.title(),
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_idle_sockets_do_not_hold_pooled_connections(small_pool_client):
    client = small_pool_client
    token = _register(client, "pooler")

    # More sockets than the pool has connections. Before the fix the third
    # socket could not even authenticate, because the first two held the pool.
    with client.websocket_connect("/api/ws") as first:
        first.send_json({"type": "auth", "token": token})
        assert first.receive_json() == {"type": "ready", "user_id": 1}

        with client.websocket_connect("/api/ws") as second:
            second.send_json({"type": "auth", "token": token})
            assert second.receive_json() == {"type": "ready", "user_id": 1}

            with client.websocket_connect("/api/ws") as third:
                third.send_json({"type": "auth", "token": token})
                assert third.receive_json() == {"type": "ready", "user_id": 1}

                assert client.engine.pool.checkedout() == 0, (
                    "idle realtime sockets are holding pooled connections"
                )

                # With the pool free, a normal read must succeed immediately.
                response = client.get(
                    "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
                )
                assert response.status_code == 200, response.text
                assert response.json()["username"] == "pooler"


def test_typing_still_relayed_with_small_pool(small_pool_client):
    """The typing path must release its connection too, not just the handshake."""
    client = small_pool_client
    ada_token = _register(client, "ada")
    grace_token = _register(client, "grace")

    response = client.post(
        "/api/conversations",
        json={"username": "grace"},
        headers={"Authorization": f"Bearer {ada_token}"},
    )
    assert response.status_code in (200, 201), response.text
    conversation_id = response.json()["id"]

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_json({"type": "auth", "token": grace_token})
        assert grace_socket.receive_json()["type"] == "ready"

        with client.websocket_connect("/api/ws") as ada_socket:
            ada_socket.send_json({"type": "auth", "token": ada_token})
            assert ada_socket.receive_json()["type"] == "ready"

            ada_socket.send_json({"type": "typing", "conversation_id": conversation_id})
            event = grace_socket.receive_json()
            assert event["type"] == "typing"
            assert event["conversation_id"] == conversation_id

            assert client.engine.pool.checkedout() == 0, (
                "a typing relay leaked a pooled connection"
            )
