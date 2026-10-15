"""In-process WebSocket hub for realtime delivery.

One process, in-memory fan-out: a message published to a user is written to
every socket that user has open. This is deliberately not a broker, so it does
not survive multiple worker processes — the seam to swap in Redis pub/sub is
``publish`` plus ``_sockets``.

Authentication happens over the socket itself: the client connects and sends a
``{"type": "auth", "token": ...}`` frame as its first message. Tokens are not
accepted as query parameters because URLs end up in access logs and browser
history.
"""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

logger = logging.getLogger("pulse.realtime")

# Frames larger than this are refused before parsing; DMs are small.
MAX_FRAME_BYTES = 16 * 1024

# A socket that has not authenticated within this window is closed.
AUTH_TIMEOUT_SECONDS = 10.0


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

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

    async def publish(self, user_id: int, payload: dict[str, Any]) -> None:
        """Send ``payload`` to every socket the user has open.

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

    async def publish_many(self, user_ids: set[int], payload: dict[str, Any]) -> None:
        await asyncio.gather(*(self.publish(uid, payload) for uid in user_ids))


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
