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
    """
    user_id = await authenticate(socket, db)
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


async def _relay_typing(db: DbSession, user_id: int, frame: dict) -> None:
    """Forward a typing indicator to the other member of the conversation.

    Membership is checked here rather than trusting the client's claim, so a
    socket cannot be used to signal typing into someone else's thread.
    """
    conversation_id = frame.get("conversation_id")
    if not isinstance(conversation_id, int):
        return

    mine = db.scalar(
        select(ConversationParticipant.id).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
        )
    )
    if mine is None:
        return

    peers = db.scalars(
        select(ConversationParticipant.user_id).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id != user_id,
        )
    ).all()

    for peer_id in peers:
        await manager.publish(
            peer_id,
            {"type": "typing", "conversation_id": conversation_id, "user_id": user_id},
        )
