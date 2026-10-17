"""Realtime WebSocket endpoint."""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..deps import DbSession, forget_last_seen, touch_last_seen
from ..models import ConversationParticipant, User
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

    # Presence is a transition, not a per-socket fact: a second tab opening or
    # closing must not flicker the status, so only the first socket in and the
    # last socket out are announced.
    was_online = manager.is_online(user_id)
    await manager.connect(user_id, socket)
    if not was_online:
        await _announce_presence(db, user_id, online=True)
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
        _stamp_last_seen(db, user_id)
        if not manager.is_online(user_id):
            await _announce_presence(db, user_id, online=False)


async def _announce_presence(db: DbSession, user_id: int, *, online: bool) -> None:
    """Tell the people who can see this user that their status changed.

    Only conversation partners are notified rather than every connected socket:
    presence is only rendered in a thread header, so broadcasting to the whole
    user base would be wasted fan-out. The frame carries the freshly stamped
    ``last_seen_at`` so a client can render the "last seen" line without a
    follow-up request.

    Runs in its own session-scoped read and swallows every failure: a presence
    frame is a nicety, and a socket that is closing must not fail because of it.
    """
    try:
        stamp = db.get(User, user_id)
        last_seen_at = stamp.last_seen_at if stamp is not None else None
        peer_ids = set(
            db.scalars(
                select(ConversationParticipant.user_id)
                .where(
                    ConversationParticipant.user_id != user_id,
                    ConversationParticipant.conversation_id.in_(
                        select(ConversationParticipant.conversation_id).where(
                            ConversationParticipant.user_id == user_id
                        )
                    ),
                )
                .distinct()
            ).all()
        )
    except Exception:  # noqa: BLE001 - presence is best-effort
        logger.debug("could not resolve presence peers for user %s", user_id, exc_info=True)
        return
    finally:
        _release(db)

    payload = {
        "type": "presence",
        "user_id": user_id,
        "is_online": online,
        "last_seen_at": last_seen_at,
    }
    for peer_id in peer_ids:
        try:
            await manager.publish(peer_id, payload)
        except Exception:  # noqa: BLE001 - one bad peer must not stop the rest
            logger.debug("presence publish failed for user %s", peer_id, exc_info=True)


def _stamp_last_seen(db: DbSession, user_id: int) -> None:
    """Record the moment a socket closed, and release the session again.

    This is the accurate end of a presence window: while the socket is open the
    user is online, and the instant it closes is the last moment they were
    definitely present. Throttling does not apply here — the close happens once
    per connection, so it is a single write rather than a per-request one.
    """
    try:
        user = db.get(User, user_id)
        if user is not None:
            # Bypass the throttle: this is the precise stamp the header shows.
            forget_last_seen(user_id)
            touch_last_seen(db, user)
    except Exception:  # noqa: BLE001 - a failed stamp must not surface to the client
        logger.debug("could not stamp last_seen for user %s", user_id, exc_info=True)
    finally:
        _release(db)


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
