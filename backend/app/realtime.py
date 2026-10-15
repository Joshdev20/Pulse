"""WebSocket hub for realtime delivery.

Fan-out is local sockets plus, when Redis is configured, a pub/sub channel that
carries each payload to the *other* worker processes. A publish therefore does
two things: deliver to sockets held by this process, and announce the payload on
the bus so every other process delivers to its own sockets. Bus messages carry
the origin process id, which is how a process recognises and ignores the echo of
its own announcement and avoids delivering twice.

Without Redis the bus is skipped entirely and this is a single-process hub, so
the app and its tests run unchanged.

Authentication happens over the socket itself: the client connects and sends a
``{"type": "auth", "token": ...}`` frame as its first message. Tokens are not
accepted as query parameters because URLs end up in access logs and browser
history.
"""

import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from .config import settings

logger = logging.getLogger("pulse.realtime")

# Frames larger than this are refused before parsing; DMs are small.
MAX_FRAME_BYTES = 16 * 1024

# A socket that has not authenticated within this window is closed.
AUTH_TIMEOUT_SECONDS = 10.0

# Cross-process fan-out channel and this process's identity on it.
BUS_CHANNEL = "pulse:ws"
PROCESS_ID = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"

# Delay before retrying a dropped bus connection.
BUS_RETRY_SECONDS = 1.0


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._bus_task: asyncio.Task | None = None

    async def connect(self, user_id: int, socket: WebSocket) -> None:
        """Register an already-accepted socket for ``user_id``."""
        async with self._lock:
            self._sockets[user_id].add(socket)

    async def disconnect(self, user_id: int, socket: WebSocket) -> None:
        async with self._lock:
            peers = self._sockets.get(user_id)
            if peers is not None:
                peers.discard(socket)
                if not peers:
                    self._sockets.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        return bool(self._sockets.get(user_id))

    def online_ids(self) -> set[int]:
        return {user_id for user_id, peers in self._sockets.items() if peers}

    async def start(self) -> None:
        """Begin listening on the cross-process bus. No-op without Redis."""
        if not settings.redis_url or self._bus_task is not None:
            return
        self._bus_task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        """Stop the bus listener, if one is running."""
        task, self._bus_task = self._bus_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def publish(self, user_id: int, payload: dict[str, Any]) -> None:
        """Deliver to local sockets and announce the payload to other processes."""
        await self._deliver(user_id, payload)
        await self._announce(user_id, payload)

    async def publish_many(self, user_ids: set[int], payload: dict[str, Any]) -> None:
        await asyncio.gather(*(self.publish(uid, payload) for uid in user_ids))

    async def _deliver(self, user_id: int, payload: dict[str, Any]) -> None:
        """Send ``payload`` to every socket the user has open in this process.

        A socket that fails to receive is dropped rather than raising, so one
        dead connection cannot break delivery to the others.
        """
        async with self._lock:
            targets = list(self._sockets.get(user_id, ()))
        if not targets:
            return
        message = json.dumps(payload, default=str)
        stale: list[WebSocket] = []
        for socket in targets:
            try:
                await socket.send_text(message)
            except Exception:  # noqa: BLE001 - any send failure means the socket is gone
                stale.append(socket)
        for socket in stale:
            await self.disconnect(user_id, socket)

    async def _announce(self, user_id: int, payload: dict[str, Any]) -> None:
        """Put the payload on the bus for the other processes. Best-effort."""
        if not settings.redis_url:
            return
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.redis_url, socket_connect_timeout=0.5)
            try:
                await client.publish(
                    BUS_CHANNEL,
                    json.dumps(
                        {"origin": PROCESS_ID, "user_id": user_id, "payload": payload},
                        default=str,
                    ),
                )
            finally:
                await client.aclose()
        except Exception:  # noqa: BLE001 - local delivery already happened
            logger.debug("realtime bus publish failed", exc_info=True)

    async def _listen(self) -> None:
        """Deliver payloads announced by other processes, reconnecting on failure."""
        import redis.asyncio as aioredis

        while True:
            try:
                client = aioredis.from_url(settings.redis_url, decode_responses=True)
                pubsub = client.pubsub()
                await pubsub.subscribe(BUS_CHANNEL)
                try:
                    async for message in pubsub.listen():
                        if message.get("type") != "message":
                            continue
                        await self._on_bus_message(message["data"])
                finally:
                    await pubsub.aclose()
                    await client.aclose()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Redis may be restarting
                logger.warning("realtime bus disconnected; retrying", exc_info=True)
                await asyncio.sleep(BUS_RETRY_SECONDS)

    async def _on_bus_message(self, raw: str) -> None:
        try:
            envelope = json.loads(raw)
        except ValueError:
            return
        # Our own announcement was already delivered locally by `publish`.
        if not isinstance(envelope, dict) or envelope.get("origin") == PROCESS_ID:
            return
        user_id = envelope.get("user_id")
        payload = envelope.get("payload")
        if isinstance(user_id, int) and isinstance(payload, dict):
            await self._deliver(user_id, payload)


manager = ConnectionManager()


async def authenticate(socket: WebSocket, db: Session) -> int | None:
    """Accept the socket, read the first frame, and return the user id it proves.

    The socket is closed if the frame is missing, oversized, malformed, or the
    token does not resolve to an active user. Accepting before authenticating is
    required to read the frame at all; a socket that fails auth is closed
    immediately and is never registered with the manager.
    """
    await socket.accept()

    try:
        raw = await asyncio.wait_for(socket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        await _safe_close(socket, 4401, "Authentication required")
        return None

    if len(raw) > MAX_FRAME_BYTES:
        await _safe_close(socket, 4400, "Frame too large")
        return None

    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        await _safe_close(socket, 4400, "Malformed frame")
        return None

    if not isinstance(frame, dict) or frame.get("type") != "auth":
        await _safe_close(socket, 4401, "Authentication required")
        return None

    token = frame.get("token")
    if not isinstance(token, str) or not token:
        await _safe_close(socket, 4401, "Authentication required")
        return None

    from .deps import _user_from_token

    user = _user_from_token(token, db)
    if user is None or not user.is_active:
        await _safe_close(socket, 4401, "Invalid credentials")
        return None
    return user.id


async def _safe_close(socket: WebSocket, code: int, reason: str) -> None:
    try:
        await socket.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 - closing an already-closed socket is fine
        pass
