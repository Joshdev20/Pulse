"""Realtime WebSocket endpoint."""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..deps import DbSession
from ..models import ConversationParticipant
from ..realtime import MAX_FRAME_BYTES, authenticate, manager

logger = logging.getLogger("pulse.realtime")

router = APIRouter(tags=["realtime"])

# Frame types a client may send after authenticating. Anything else is ignored
# rather than echoed, so the socket is not a general-purpose relay.
ALLOWED_INBOUND = {"typing", "read", "ping"}


@router.websocket("/api/ws")
async def realtime_socket(socket: WebSocket, db: DbSession) -> None:
    """Authenticate the first frame, then push events for this user.

    The client sends ``{"type": "auth", "token": "..."}`` first. From then on
    the server pushes ``notification``, ``message`` and ``typing`` frames.

    The request-scoped session is released as soon as authentication finishes.
    A socket outlives its session by minutes or hours, and a session that stays
    open pins a pooled connection the whole time; a handful of idle sockets
    would exhaust the pool and stall every HTTP request behind a ~30s pool
    timeout. Each later database touch closes the session again for the same
    reason.
    """
    user_id = await authenticate(socket, db)
    _release(db)
    if user_id is None:
        return

    await manager.connect(user_id, socket)
    try:
        await socket.send_text(json.dumps({"type": "ready", "user_id": user_id}))
        while True:
            raw = await socket.receive_text()
            if len(raw) > MAX_FRAME_BYTES:
                continue
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            if kind == "ping":
                await socket.send_text(json.dumps({"type": "pong"}))
            elif kind == "typing":
                await _relay_typing(db, user_id, frame)
            elif kind == "read":
                await socket.send_text(json.dumps({"type": "ack", "for": "read"}))
            elif kind not in ALLOWED_INBOUND:
                logger.debug("Ignoring unknown frame type %r from user %s", kind, user_id)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a broken socket must not take down the app
        logger.exception("Realtime socket failed for user %s", user_id)
    finally:
        await manager.disconnect(user_id, socket)


def _release(db: DbSession) -> None:
    """Return the session's pooled connection without discarding the session."""
    db.close()


async def _relay_typing(db: DbSession, user_id: int, frame: dict) -> None:
    """Forward a typing indicator to the other member of the conversation.

    Membership is checked here rather than trusting the client's claim, so a
    socket cannot be used to signal typing into someone else's thread.
    """
    conversation_id = frame.get("conversation_id")
    if not isinstance(conversation_id, int):
        return

    try:
        peers = _typing_peers(db, user_id, conversation_id)
    finally:
        _release(db)

    for peer_id in peers:
        await manager.publish(
            peer_id,
            {"type": "typing", "conversation_id": conversation_id, "user_id": user_id},
        )


def _typing_peers(db: DbSession, user_id: int, conversation_id: int) -> list[int]:
    """Other participants of `conversation_id`, or [] if the sender is not in it."""
    mine = db.scalar(
        select(ConversationParticipant.id).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
        )
    )
    if mine is None:
        return []

    return list(
        db.scalars(
            select(ConversationParticipant.user_id).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id != user_id,
            )
        ).all()
    )
