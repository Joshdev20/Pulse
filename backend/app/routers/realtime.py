"""Realtime WebSocket endpoint."""

import json
import logging
from datetime import timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..deps import DbSession, forget_last_seen, touch_last_seen
from .. import push
from ..models import Conversation, ConversationParticipant, Message, User, utcnow
from ..realtime import MAX_FRAME_BYTES, authenticate, manager
from .messages import CALL_RINGING, CALL_ACTIVE, serialize_messages

logger = logging.getLogger("pulse.realtime")

router = APIRouter(tags=["realtime"])

# Frame types a client may send after authenticating. Anything else is ignored
# rather than echoed, so the socket is not a general-purpose relay.
ALLOWED_INBOUND = {"typing", "read", "ping", "call"}


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
            elif kind == "call":
                await _relay_call(db, user_id, frame)
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


# The call lifecycle as relayed frames. A "signal" carries an opaque SDP or ICE
# blob between the two peers; the rest are the state transitions the callee and
# caller need to agree on. Anything outside this set is dropped.
CALL_ACTIONS = {"offer", "answer", "ice", "accept", "decline", "hangup"}

# SDP and ICE payloads are a few kilobytes; anything far larger is not one.
MAX_CALL_PAYLOAD_CHARS = 12_000


async def _relay_call(db: DbSession, user_id: int, frame: dict) -> None:
    """Forward one call-control frame, and keep the thread's call log in step.

    The server still never parses SDP or ICE: media stays peer to peer. What it
    does now is record the *fact* of a call, because a call is part of the
    conversation and both sides expect to find it there afterwards — a missed
    call with no trace is the complaint this answers.

    The log row is a message row, so it sorts into the history for free. It is
    created on the offer, which is the first frame of any call, and updated by
    the frames that decide how the call ended. Because the offer is recorded
    before the callee answers, a call nobody picked up still leaves a "missed"
    entry rather than vanishing.

    Membership is checked here rather than trusted from the client for the same
    reason as typing: otherwise the socket would be a way to ring a stranger.
    """
    conversation_id = frame.get("conversation_id")
    action = frame.get("action")
    if not isinstance(conversation_id, int) or action not in CALL_ACTIONS:
        return

    payload = frame.get("payload")
    if isinstance(payload, str) and len(payload) > MAX_CALL_PAYLOAD_CHARS:
        return

    mode = frame.get("mode") if frame.get("mode") in {"audio", "video"} else "audio"
    call_ref = frame.get("call_id")
    if not isinstance(call_ref, str) or not call_ref:
        # Without an id the frames of one call cannot be told from another, so
        # the log would be guesses. The signalling itself still works.
        call_ref = None
    elif len(call_ref) > 32:
        call_ref = call_ref[:32]

    # Bound before the try so a failed membership lookup leaves nothing to relay
    # rather than raising on an unbound name below.
    peers: list[int] = []
    try:
        peers = _typing_peers(db, user_id, conversation_id)
        if not peers:
            return
        if call_ref is not None:
            await _record_call(db, user_id, conversation_id, call_ref, action, mode)
    except Exception:  # noqa: BLE001 - logging a call must not break signalling
        logger.debug("could not record call for user %s", user_id, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        _release(db)

    outgoing = {
        "type": "call",
        "conversation_id": conversation_id,
        "from": user_id,
        "action": action,
        "call_id": frame.get("call_id"),
        "mode": mode,
        "payload": payload if isinstance(payload, str) else None,
    }
    for peer_id in peers:
        await manager.publish(peer_id, outgoing)


async def _record_call(
    db: DbSession,
    user_id: int,
    conversation_id: int,
    call_ref: str,
    action: str,
    mode: str,
) -> None:
    """Create or advance the log row for one call.

    One row per call, keyed by `call_ref`. Which side writes which state is
    decided by who sent the frame, not by who is asking: the offer is always the
    caller's, the decline or the answer is always the callee's. That is what
    makes the stored state a fact rather than a claim.
    """
    row = db.scalar(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.call_ref == call_ref,
        )
    )

    if action == "offer":
        if row is not None:
            # A repeated offer is a peer that reconnected mid-call. The call is
            # already logged; re-logging it would put two entries in the thread.
            return
        row = Message(
            conversation_id=conversation_id,
            sender_id=user_id,
            body="",
            call_mode=mode,
            call_state=CALL_RINGING,
            call_ref=call_ref,
        )
        db.add(row)
        conversation = db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.last_message_at = row.created_at or utcnow()
        db.commit()
        await _publish_call_entry(db, row, conversation_id)
        return

    if row is None:
        # A frame for a call this process never saw start (a restart, or the
        # offer arrived before the row was written). Nothing to advance.
        return

    # Only the callee can answer or decline; only either side can hang up.
    caller_side = row.sender_id == user_id
    changed = False

    if action == "answer" and not caller_side:
        if row.call_state == CALL_RINGING:
            row.call_state = CALL_ACTIVE
            row.call_connected_at = utcnow()
            changed = True
    elif action == "accept" and not caller_side:
        if row.call_state == CALL_RINGING:
            row.call_state = CALL_ACTIVE
            row.call_connected_at = row.call_connected_at or utcnow()
            changed = True
    elif action == "decline" and not caller_side:
        if row.call_state in (CALL_RINGING, CALL_ACTIVE):
            row.call_state = "declined"
            changed = True
    elif action == "hangup":
        # Either side hanging up ends the call. Which final state it lands in
        # depends on how far it got: a call that never connected and was
        # abandoned by the caller is "cancelled"; one that never connected and
        # was dropped by the callee is "declined"; one that had media is
        # "answered" with its duration.
        if row.call_state == CALL_RINGING:
            row.call_state = "cancelled" if caller_side else "declined"
            changed = True
        elif row.call_state == CALL_ACTIVE:
            row.call_state = "answered"
            row.call_seconds = _connected_seconds(row)
            changed = True

    if not changed:
        return
    db.commit()
    await _publish_call_entry(db, row, conversation_id)


def _connected_seconds(row: Message) -> int:
    """Whole seconds of talk time, from when media connected to now."""
    started = row.call_connected_at
    if started is None:
        return 0
    if getattr(started, "tzinfo", None) is not None:
        started = started.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0, int((utcnow().replace(tzinfo=None) - started).total_seconds()))


async def _publish_call_entry(db: DbSession, row: Message, conversation_id: int) -> None:
    """Push the call entry to both participants so it appears without a reload.

    Serialized per reader like any message, because the wording of the entry is
    relative: the same row is "Outgoing" to the caller and "Missed" to the
    person who did not answer.
    """
    participant_ids = list(
        db.scalars(
            select(ConversationParticipant.user_id).where(
                ConversationParticipant.conversation_id == conversation_id
            )
        ).all()
    )
    for participant_id in participant_ids:
        viewer = db.get(User, participant_id)
        if viewer is None:
            continue
        payload = serialize_messages(db, [row], viewer)[0].model_dump(mode="json")
        await manager.publish(
            participant_id,
            {
                "type": "message",
                "conversation_id": conversation_id,
                "message": payload,
            },
        )
        # A call the recipient never picked up has to reach a closed browser:
        # the socket above only covers a tab that is already open, which is
        # exactly the case a missed call is not. The stored state is `cancelled`
        # when the caller gave up while it rang, which is `missed` from the
        # callee's side — the side this alert is for. A call the callee declined
        # is not pushed back to them: they were there, and they know.
        if participant_id != row.sender_id and row.call_state == "cancelled":
            push.schedule_notify(
                participant_id,
                push.message_payload(db, row, conversation_id, participant_id),
            )
