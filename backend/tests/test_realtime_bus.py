"""Cross-process realtime fan-out: envelope handling and echo suppression."""

import json

import pytest

from app import realtime

# anyio ships with Starlette, so the async cases run without another dependency.
pytestmark = pytest.mark.anyio


class FakeSocket:
    """Minimal stand-in for a WebSocket that records what it was sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, message: str) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.fixture
def manager():
    return realtime.ConnectionManager()


async def test_publish_delivers_to_local_sockets(manager, monkeypatch):
    monkeypatch.setattr(realtime.settings, "redis_url", None)
    socket = FakeSocket()
    await manager.connect(7, socket)

    await manager.publish(7, {"type": "message", "body": "hi"})

    assert len(socket.sent) == 1
    assert json.loads(socket.sent[0])["body"] == "hi"


async def test_publish_reaches_only_the_addressed_user(manager, monkeypatch):
    monkeypatch.setattr(realtime.settings, "redis_url", None)
    mine, theirs = FakeSocket(), FakeSocket()
    await manager.connect(1, mine)
    await manager.connect(2, theirs)

    await manager.publish(1, {"type": "message"})

    assert len(mine.sent) == 1
    assert theirs.sent == []


async def test_bus_message_from_another_process_is_delivered(manager):
    socket = FakeSocket()
    await manager.connect(5, socket)

    await manager._on_bus_message(
        json.dumps({"origin": "other:1234", "user_id": 5, "payload": {"type": "message"}})
    )

    assert len(socket.sent) == 1


async def test_own_announcement_is_not_delivered_twice(manager):
    """The echo of our own publish must be ignored; local delivery already ran."""
    socket = FakeSocket()
    await manager.connect(5, socket)

    await manager._on_bus_message(
        json.dumps({"origin": realtime.PROCESS_ID, "user_id": 5, "payload": {"type": "message"}})
    )

    assert socket.sent == []


async def test_malformed_bus_messages_are_ignored(manager):
    socket = FakeSocket()
    await manager.connect(5, socket)

    await manager._on_bus_message("not json")
    await manager._on_bus_message(json.dumps([1, 2, 3]))
    await manager._on_bus_message(json.dumps({"origin": "x", "user_id": "5"}))
    await manager._on_bus_message(json.dumps({"origin": "x", "user_id": 5, "payload": "nope"}))

    assert socket.sent == []


async def test_disconnect_removes_only_that_socket(manager, monkeypatch):
    monkeypatch.setattr(realtime.settings, "redis_url", None)
    first, second = FakeSocket(), FakeSocket()
    await manager.connect(3, first)
    await manager.connect(3, second)

    await manager.disconnect(3, first)
    await manager.publish(3, {"type": "message"})

    assert first.sent == []
    assert len(second.sent) == 1


async def test_bus_is_not_started_without_redis(manager, monkeypatch):
    monkeypatch.setattr(realtime.settings, "redis_url", None)
    await manager.start()
    assert manager._bus_task is None


async def test_bus_is_not_started_when_redis_is_unreachable(manager, monkeypatch):
    """A configured but absent Redis must not start a retry-forever listener.

    The default configuration points at localhost:6379, so a host without Redis
    used to spawn a listener that failed every second and logged a full
    traceback each time, for a feature that is optional by design.
    """
    # Port 1 is never listening, so the connection is refused immediately.
    monkeypatch.setattr(realtime.settings, "redis_url", "redis://127.0.0.1:1/0")

    await manager.start()

    assert manager._bus_task is None
    assert manager._bus_active is False


async def test_publish_still_works_locally_when_redis_is_unreachable(manager, monkeypatch):
    """Delivery to local sockets must be unaffected by a missing bus."""
    monkeypatch.setattr(realtime.settings, "redis_url", "redis://127.0.0.1:1/0")
    await manager.start()

    socket = FakeSocket()
    await manager.connect(4, socket)

    await manager.publish(4, {"type": "message", "body": "local only"})

    assert len(socket.sent) == 1
    assert json.loads(socket.sent[0])["body"] == "local only"
