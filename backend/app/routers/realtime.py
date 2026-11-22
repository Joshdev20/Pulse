"""Realtime WebSocket endpoint."""

import asyncio
import json
import logging
import time
from datetime import timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import DbSession, forget_last_seen, touch_last_seen
from .. import push
from ..models import Conversation, ConversationParticipant, Message, User, utcnow
from ..realtime import MAX_FRAME_BYTES, authenticate, frame_json, manager
from .messages import CALL_RINGING, CALL_ACTIVE, serialize_messages

logger = logging.getLogger("pulse.realtime")

router = APIRouter(tags=["realtime"])

# How long a socket may stay silent before it is treated as gone.
#
# A phone that sleeps, a laptop that changes network or a tab that is killed
# commonly leaves a socket that never sends a close frame. Without a read
# timeout that socket stays registered for as long as the process lives: the
# user is shown as online forever and, because no transition is ever announced,
# their partner's header keeps whatever "last seen" it happened to hold. The
# client pings every ten seconds, so this much silence means the peer is gone.
SOCKET_IDLE_TIMEOUT_SECONDS = 75.0

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
        # No write here on purpose: this is the connect path, and a database
        # round trip per connect is what made connects cost seconds. The close
        # below stamps last seen, and the idle timeout means a socket that dies
        # silently still reaches it.
        await _announce_presence(db, user_id, online=True)
    # Per-socket, so it dies with the connection it describes.
    peer_cache: dict[int, tuple[float, list[int]]] = {}
    try:
        # Answered from memory, not the database. This used to run a query on
        # every connect, which is a round trip against a remote database for
        # every tab, reconnect and navigation — it blocked the event loop for
        # 1.3s per connect and dragged ordinary reads from 2.3s to 7.8s, which
        # read as slow sends, late message rendering and media that appeared to
        # reload. Nothing here may touch the database on the connect path.
        replay = ringing_call_frames(user_id)
        await socket.send_text(frame_json({"type": "ready", "user_id": user_id}))
        # A callee who opened the app from a ring notification missed the offer:
        # it was relayed while no socket was open. Replaying it now is what lets
        # that notification lead to an answer rather than a dead call.
        for frame in replay:
            await socket.send_text(frame_json(frame))
        while True:
            try:
                raw = await asyncio.wait_for(
                    socket.receive_text(), timeout=SOCKET_IDLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                # Dropping out of the loop runs the `finally` below, which
                # stamps last seen and announces the transition to offline.
                break
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
                await socket.send_text(frame_json({"type": "pong"}))
            elif kind == "typing":
                await _relay_typing(db, user_id, frame, peer_cache)
            elif kind == "call":
                await _relay_call(db, user_id, frame, peer_cache)
            elif kind == "read":
                await socket.send_text(frame_json({"type": "ack", "for": "read"}))
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
        # Serialized by `frame_json` on the way out, which attaches UTC to the
        # naive value SQLite hands back.
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


async def _relay_typing(
    db: DbSession,
    user_id: int,
    frame: dict,
    cache: dict[int, tuple[float, list[int]]] | None = None,
) -> None:
    """Forward a typing indicator to the other member of the conversation.

    Membership is checked here rather than trusting the client's claim, so a
    socket cannot be used to signal typing into someone else's thread.
    """
    conversation_id = frame.get("conversation_id")
    if not isinstance(conversation_id, int):
        return

    try:
        peers = _cached_peers(db, user_id, conversation_id, cache)
    finally:
        _release(db)

    for peer_id in peers:
        await manager.publish(
            peer_id,
            {"type": "typing", "conversation_id": conversation_id, "user_id": user_id},
        )


def _typing_peers(db: DbSession, user_id: int, conversation_id: int) -> list[int]:
    """Other participants of `conversation_id`, or [] if the sender is not in it."""
    # One query rather than two. This runs on the path of every relayed frame,
    # and a call sends a dozen ICE frames: on a hosted Postgres each extra round
    # trip is real latency added to call setup, which is the "Connecting…" that
    # lingers.
    members = list(
        db.scalars(
            select(ConversationParticipant.user_id).where(
                ConversationParticipant.conversation_id == conversation_id
            )
        ).all()
    )
    if user_id not in members:
        return []
    return [member for member in members if member != user_id]


# How long a resolved peer list is reused for one socket. A typing indicator
# fires on every keystroke and a call sends a dozen ICE frames, so without this
# each of those was a database round trip on the event loop — hundreds of them
# for one message, all of it blocking every other request. Membership cannot
# change mid-call in this app, and the window is short enough that a revoked
# member is cut off promptly.
PEERS_CACHE_TTL_SECONDS = 10.0
_PEERS_CACHE_MAX = 16


def _cached_peers(
    db: DbSession,
    user_id: int,
    conversation_id: int,
    cache: dict[int, tuple[float, list[int]]] | None,
) -> list[int]:
    """`_typing_peers`, memoised for one socket for a few seconds."""
    if cache is not None:
        hit = cache.get(conversation_id)
        if hit is not None and (time.monotonic() - hit[0]) < PEERS_CACHE_TTL_SECONDS:
            return hit[1]
    peers = _typing_peers(db, user_id, conversation_id)
    if cache is not None:
        if len(cache) >= _PEERS_CACHE_MAX:
            # Oldest out. A socket only ever types or calls in a handful of
            # threads, so this bounds the dict without an LRU structure.
            oldest = min(cache, key=lambda key: cache[key][0])
            cache.pop(oldest, None)
        cache[conversation_id] = (time.monotonic(), peers)
    return peers


# The call lifecycle as relayed frames. A "signal" carries an opaque SDP or ICE
# blob between the two peers; the rest are the state transitions the callee and
# caller need to agree on. Anything outside this set is dropped.
CALL_ACTIONS = {"offer", "answer", "ice", "accept", "decline", "hangup"}

# SDP and ICE payloads are a few kilobytes; anything far larger is not one.
MAX_CALL_PAYLOAD_CHARS = 12_000


async def _relay_call(
    db: DbSession,
    user_id: int,
    frame: dict,
    cache: dict[int, tuple[float, list[int]]] | None = None,
) -> None:
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
        peers = _cached_peers(db, user_id, conversation_id, cache)
        if not peers:
            return
        # ICE candidates only ever pass through: they are the bulk of a call's
        # frames and none of them changes the logged state, so looking the row up
        # for each one is a round trip spent to learn nothing.
        if call_ref is not None and action != "ice":
            await _record_call(
                db, user_id, conversation_id, call_ref, action, mode, peers
            )
        # Held for a callee who has not connected yet, and dropped once the call
        # is decided: there is nothing left to replay after that. Only the
        # caller's candidates are held — the callee is the side being replayed
        # to, so its own candidates travel to a caller who is already listening.
        if call_ref is not None:
            if action == "offer":
                _remember_ringing_call(
                    call_ref, conversation_id, user_id, mode,
                    payload if isinstance(payload, str) else None,
                    peers,
                )
            elif action == "ice":
                if _ringing_calls.get(call_ref, {}).get("caller_id") == user_id:
                    _remember_ringing_ice(
                        call_ref, payload if isinstance(payload, str) else None
                    )
            else:
                _forget_ringing_call(call_ref)
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


# Signalling for calls that are ringing right now, so a callee who opens the app
# from a ring notification can still answer.
#
# The offer SDP and the caller's ICE candidates are relayed and never stored: a
# socket that was not connected when they went past never sees them, and the
# call cannot be completed without them. Keeping the last offer per call for the
# length of the ring window is what makes the notification actionable rather
# than a message saying a call is happening that the reader cannot join.
#
# Process-local, like presence: the deployment runs one worker, and a restart
# ends every call anyway. A callee on another worker is pushed to but cannot be
# replayed to, which is the same limitation presence already has.
RINGING_CALLS_MAX = 32
RINGING_ICE_MAX = 48
_ringing_calls: dict[str, dict] = {}


def _remember_ringing_call(
    call_ref: str,
    conversation_id: int,
    caller_id: int,
    mode: str,
    payload: str | None,
    callee_ids: list[int],
) -> None:
    """Hold the offer for a ringing call so it can be replayed on connect."""
    if len(_ringing_calls) >= RINGING_CALLS_MAX:
        oldest = min(_ringing_calls, key=lambda key: _ringing_calls[key]["at"])
        _ringing_calls.pop(oldest, None)
    _ringing_calls[call_ref] = {
        "conversation_id": conversation_id,
        "caller_id": caller_id,
        "callee_ids": [cid for cid in callee_ids if cid != caller_id],
        "mode": mode,
        "payload": payload,
        "ice": [],
        "at": time.monotonic(),
    }


def _forget_ringing_call(call_ref: str | None) -> None:
    if call_ref:
        _ringing_calls.pop(call_ref, None)


def _remember_ringing_ice(call_ref: str, payload: str | None) -> None:
    """Keep a caller's candidates for a call still ringing, so a replay has them.

    A candidate is never re-sent, so one that arrived while the callee had no
    socket would otherwise be lost and the call could connect with no usable
    pair.
    """
    held = _ringing_calls.get(call_ref)
    if held is None or payload is None:
        return
    if len(held["ice"]) < RINGING_ICE_MAX:
        held["ice"].append(payload)


def ringing_call_frames(user_id: int) -> list[dict]:
    """The frames to replay to ``user_id`` for calls still ringing at them.

    Answered from the in-memory buffer alone, with no database read. That is
    deliberate: this runs on every socket connect, and a query here meant every
    tab open, every reconnect and every navigation paid a round trip against a
    database an ocean away. Measured on production, that stalled the event loop
    for 1.3s per connect and stretched ordinary reads from 2.3s to 7.8s, because
    the query blocked the loop that every other socket and request shares.

    The buffer is a complete record of what can be replayed: a call is only
    rememberable when this process relayed its offer, and it is forgotten the
    moment the call is decided. So "held and fresh" is exactly "live", with no
    need to confirm against the log. A row the sweeper finalised belongs to a
    call this process has already dropped.
    """
    frames: list[dict] = []
    for call_ref, held in list(_ringing_calls.items()):
        if user_id not in held["callee_ids"]:
            continue
        if time.monotonic() - held["at"] > CALL_RING_STALE_SECONDS:
            _ringing_calls.pop(call_ref, None)
            continue
        base = {
            "type": "call",
            "conversation_id": held["conversation_id"],
            "from": held["caller_id"],
            "call_id": call_ref,
            "mode": held["mode"],
        }
        frames.append({**base, "action": "offer", "payload": held["payload"]})
        for candidate in held["ice"]:
            frames.append({**base, "action": "ice", "payload": candidate})
    return frames


def _push_ringing_call(
    db: Session, row: Message, conversation_id: int, callee_ids: list[int]
) -> None:
    """Alert a callee who has no tab open that a call is ringing *now*.

    Until this existed the only call alert was the missed one, written after the
    caller had given up. Someone with Pulse closed therefore learned about every
    call too late to answer it, which is the difference between a missed call and
    an answered one.

    Takes the peer list the relay already resolved rather than querying for it
    again: this runs on the offer path, and a second participants lookup per
    call is the round trip the peer cache exists to avoid.

    Skipped when the callee already holds a socket: that tab is being sent the
    offer frame and rings in-app, and an OS notification on top of a ringing
    screen is noise. Presence is per-process, so a callee connected to another
    worker is treated as away and pushed to — the harmless direction, since the
    in-app ring still happens when the frame arrives.
    """
    for callee_id in callee_ids:
        if callee_id == row.sender_id or manager.is_online(callee_id):
            continue
        push.schedule_notify(callee_id, push.incoming_call_payload(db, row, conversation_id))


async def _record_call(
    db: DbSession,
    user_id: int,
    conversation_id: int,
    call_ref: str,
    action: str,
    mode: str,
    peers: list[int] | None = None,
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
        _push_ringing_call(db, row, conversation_id, peers or [])
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
    for participant_id, payload in _call_entry_frames(db, row, conversation_id):
        await manager.publish(participant_id, payload)


def _call_entry_frames(
    db: Session, row: Message, conversation_id: int
) -> list[tuple[int, dict]]:
    """Build the per-reader frames for a call entry, without sending them.

    Split from the publish so the blocking part can run in a worker thread and
    the awaiting part on the event loop: the sweeper calls this from a thread
    and hands the result back to publish. Queuing the push alert also happens
    here, because `schedule_notify` only appends to a queue and is safe to call
    from either.
    """
    frames: list[tuple[int, dict]] = []
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
        frames.append(
            (
                participant_id,
                {
                    "type": "message",
                    "conversation_id": conversation_id,
                    "message": payload,
                },
            )
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
    return frames


# How stale a transient call row may be before it is finalised.
#
# These mirror the read-time expiry in `messages._call_log`, and exist for the
# same reason: the frame that ends a call (a decline, a hangup) travels over the
# same socket as everything else, so it is lost whenever the peer closes the tab,
# loses the network, or the process restarts mid-call. The row is then left
# `ringing` and both sides read it as a call still in progress.
#
# Read-time expiry alone is not enough. It only applies to the *next* read, so
# the thread the reader already has open keeps showing "Calling…" until they
# navigate away and back. Finalising here — and publishing the finished entry —
# is what turns that into a live update.
CALL_RING_STALE_SECONDS = 90
CALL_ACTIVE_STALE_SECONDS = 6 * 60 * 60

# How often the sweeper looks for abandoned rows.
CALL_SWEEP_INTERVAL_SECONDS = 15


async def sweep_stale_calls(db: Session | None = None) -> int:
    """Finalise call rows whose end frame never arrived, and publish them.

    Returns how many rows were resolved, which is what the tests assert on. Each
    row is committed and published on its own so one bad row cannot stop the
    rest of the sweep, and so the readers of a resolved call hear about it
    immediately rather than at the end of a batch.

    A session may be passed in so a test can sweep the database it controls;
    the running app passes nothing and gets its own.

    The database work runs in a worker thread. SQLAlchemy is synchronous, and
    this is called from a background task on the event loop: a query against a
    remote Postgres takes tens of milliseconds at best and seconds when the
    pool is busy, and every one of those milliseconds is time the loop is not
    answering anything else. A sweep that ran inline stalled `/api/health` long
    enough for Render's health check to fail and restart the service.

    The two halves need different threads: the queries are blocking and belong
    off the event loop, the sends are `await`ing and belong on it.
    """
    resolved, frames = await asyncio.to_thread(_sweep_stale_calls_sync, db)
    for participant_id, payload in frames:
        try:
            await manager.publish(participant_id, payload)
        except Exception:  # noqa: BLE001 - one bad peer must not stop the rest
            logger.debug("could not publish resolved call to %s", participant_id, exc_info=True)
    return resolved


def _sweep_stale_calls_sync(db: Session | None = None) -> tuple[int, list[tuple[int, dict]]]:
    """The blocking half of `sweep_stale_calls`; see it for the contract.

    Returns the number of rows resolved and the frames that announce them. The
    caller publishes those; this function never awaits, so it is safe to run in
    a worker thread.
    """
    own_session = db is None
    if db is None:
        from ..database import SessionLocal

        db = SessionLocal()
    resolved = 0
    frames: list[tuple[int, dict]] = []
    try:
        # One bounded read. The rows this can match are the calls that are
        # ringing or connected *right now*, which is a handful at most, so the
        # scan stays small even on a busy day.
        stale = list(
            db.scalars(
                select(Message).where(
                    Message.call_ref.is_not(None),
                    Message.call_state.in_((CALL_RINGING, CALL_ACTIVE)),
                )
            ).all()
        )
        now = utcnow().replace(tzinfo=None)
        for row in stale:
            created = row.created_at
            if created is not None and getattr(created, "tzinfo", None) is not None:
                created = created.astimezone(timezone.utc).replace(tzinfo=None)
            if created is None:
                continue
            age = (now - created).total_seconds()
            if row.call_state == CALL_RINGING and age > CALL_RING_STALE_SECONDS:
                # A call nobody ever answered. Which wording each side sees is
                # resolved per reader in `messages._call_log`, so the neutral
                # stored state is what gets written here.
                row.call_state = "cancelled"
            elif row.call_state == CALL_ACTIVE and age > CALL_ACTIVE_STALE_SECONDS:
                row.call_state = "answered"
                row.call_seconds = _connected_seconds(row)
            else:
                continue
            db.commit()
            resolved += 1
            try:
                frames.extend(_call_entry_frames(db, row, row.conversation_id))
            except Exception:  # noqa: BLE001 - publishing must not stop the sweep
                logger.debug("could not build frame for resolved call %s", row.id, exc_info=True)
    except Exception:  # noqa: BLE001 - a sweep failure must not kill the loop
        logger.debug("call sweep failed", exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        if own_session:
            db.close()
    return resolved, frames
