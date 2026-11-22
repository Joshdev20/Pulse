"""Tests for direct messages, conversations and the realtime socket."""

import asyncio
import json

from sqlalchemy import event


def start(client, headers, username: str):
    return client.post("/api/conversations", headers=headers, json={"username": username})


def send(client, headers, conversation_id: int, body: str):
    return client.post(
        f"/api/conversations/{conversation_id}/messages", headers=headers, json={"body": body}
    )


def recv_frame(socket, kind: str) -> dict:
    """Read frames until one of ``kind`` arrives, skipping presence noise.

    A socket also receives ``presence`` frames as its conversation partners
    come and go, and one of those can land ahead of the frame a test is
    waiting for.
    """
    for _ in range(10):
        frame = json.loads(socket.receive_text())
        if frame["type"] == kind:
            return frame
    raise AssertionError(f"no {kind!r} frame arrived")


def test_conversations_require_authentication(client):
    assert client.get("/api/conversations").status_code == 401
    assert start(client, {}, "ada").status_code == 401


def test_start_conversation_and_send(client, make_user):
    ada, ada_user = make_user("ada")
    grace, _ = make_user("grace")

    conversation = start(client, ada, "grace")
    assert conversation.status_code == 201, conversation.text
    cid = conversation.json()["id"]
    assert conversation.json()["partner"]["username"] == "grace"

    sent = send(client, ada, cid, "hello there")
    assert sent.status_code == 201, sent.text
    assert sent.json()["body"] == "hello there"
    assert sent.json()["mine"] is True
    assert sent.json()["sender"]["id"] == ada_user["id"]

    # The recipient sees it from their side, marked as not theirs.
    history = client.get(f"/api/conversations/{cid}/messages", headers=grace).json()
    assert [m["body"] for m in history] == ["hello there"]
    assert history[0]["mine"] is False


def test_start_conversation_is_idempotent(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    first = start(client, ada, "grace").json()["id"]
    second = start(client, ada, "grace").json()["id"]
    assert first == second


def test_cannot_message_self(client, make_user):
    ada, _ = make_user("ada")
    assert start(client, ada, "ada").status_code == 400


def test_cannot_start_conversation_with_unknown_user(client, make_user):
    ada, _ = make_user("ada")
    assert start(client, ada, "nobody").status_code == 404


def test_non_participant_cannot_read_or_write(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    make_user("linus")

    cid = start(client, ada, "grace").json()["id"]
    linus, _ = make_user("alan")  # a fourth account, not a participant

    # 404 rather than 403 so a private thread's existence is not confirmed.
    assert client.get(f"/api/conversations/{cid}/messages", headers=linus).status_code == 404
    assert send(client, linus, cid, "intruding").status_code == 404


def test_empty_message_is_rejected(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    assert send(client, ada, cid, "   ").status_code == 400


def test_unread_count_and_mark_read(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    send(client, ada, cid, "one")
    send(client, ada, cid, "two")

    assert client.get("/api/conversations/unread-count", headers=grace).json()["count"] == 2

    inbox = client.get("/api/conversations", headers=grace).json()
    assert inbox[0]["unread_count"] == 2
    assert inbox[0]["last_message"]["body"] == "two"

    assert client.post(f"/api/conversations/{cid}/read", headers=grace).status_code == 200
    assert client.get("/api/conversations/unread-count", headers=grace).json()["count"] == 0


def test_sender_own_messages_are_not_unread(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    send(client, ada, cid, "mine")
    assert client.get("/api/conversations/unread-count", headers=ada).json()["count"] == 0


def test_inbox_is_ordered_by_recent_activity(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    linus, _ = make_user("linus")

    with_grace = start(client, ada, "grace").json()["id"]
    with_linus = start(client, ada, "linus").json()["id"]

    send(client, ada, with_grace, "first")
    send(client, ada, with_linus, "second")

    inbox = client.get("/api/conversations", headers=ada).json()
    assert [c["id"] for c in inbox] == [with_linus, with_grace]


def test_message_history_pagination(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    for index in range(10):
        send(client, ada, cid, f"m{index}")

    page = client.get(
        f"/api/conversations/{cid}/messages?limit=4", headers=grace
    ).json()
    assert [m["body"] for m in page] == ["m6", "m7", "m8", "m9"]

    older = client.get(
        f"/api/conversations/{cid}/messages?limit=4&before_id={page[0]['id']}", headers=grace
    ).json()
    assert [m["body"] for m in older] == ["m2", "m3", "m4", "m5"]


def test_message_can_carry_media(client, make_user, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    import io

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    media = client.post(
        "/api/media", headers=ada, files={"file": ("x.png", io.BytesIO(png), "image/png")}
    ).json()
    cid = start(client, ada, "grace").json()["id"]

    sent = client.post(
        f"/api/conversations/{cid}/messages",
        headers=ada,
        json={"body": "", "media_ids": [media["id"]]},
    )
    assert sent.status_code == 201, sent.text
    assert len(sent.json()["media"]) == 1

    # The recipient, being a participant, can fetch the attachment.
    assert client.get(f"/api/media/{media['id']}", headers=grace).status_code == 200


def test_voice_note_can_be_sent_as_a_message(client, make_user, tmp_path, monkeypatch):
    """The whole voice-note path: record -> upload -> send -> play back.

    A recording is uploaded as audio, referenced by a message with an empty
    body, and fetched by the recipient. This is the flow the recorder UI drives,
    so it is the one that has to work end to end.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    import io
    import struct
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(struct.pack("<h", 0) * 1600)

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")

    media = client.post(
        "/api/media",
        headers=ada,
        files={"file": ("voice-note.wav", io.BytesIO(buffer.getvalue()), "audio/wav")},
        data={"alt_text": "Voice note"},
    )
    assert media.status_code == 201, media.text
    assert media.json()["kind"] == "audio"

    cid = start(client, ada, "grace").json()["id"]
    sent = client.post(
        f"/api/conversations/{cid}/messages",
        headers=ada,
        json={"body": "", "media_ids": [media.json()["id"]]},
    )
    assert sent.status_code == 201, sent.text
    payload = sent.json()
    # A voice note has no text; the clip is the whole message.
    assert payload["body"] == ""
    assert [item["kind"] for item in payload["media"]] == ["audio"]

    # The recipient can play it back, and it is served as audio.
    fetched = client.get(f"/api/media/{media.json()['id']}", headers=grace)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("audio/")


def test_websocket_requires_authentication(client):
    """A socket that never authenticates must be closed, not left open."""
    with client.websocket_connect("/api/ws") as socket:
        socket.send_text(json.dumps({"type": "auth", "token": "not-a-real-token"}))
        # The server closes on a bad token; the next receive raises.
        try:
            socket.receive_text()
            closed = False
        except Exception:
            closed = True
        assert closed


def test_websocket_delivers_message_to_connected_recipient(client, make_user):
    """A message sent over REST reaches the recipient's open socket."""
    ada, ada_user = make_user("ada")
    grace, _ = make_user("grace")
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]
    cid = start(client, ada, "grace").json()["id"]

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        assert json.loads(grace_socket.receive_text())["type"] == "ready"

        send(client, ada, cid, "over the wire")

        frame = json.loads(grace_socket.receive_text())
        assert frame["type"] == "message"
        assert frame["conversation_id"] == cid
        assert frame["message"]["body"] == "over the wire"
        assert frame["message"]["sender"]["id"] == ada_user["id"]
        assert frame["message"]["mine"] is False


def test_websocket_echoes_message_to_the_senders_other_tab(client, make_user):
    """The sender's own sockets are notified too, marked as theirs.

    Without this, a second tab or device belonging to the sender would stay
    stale until a manual refresh.
    """
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]
    cid = start(client, ada, "grace").json()["id"]

    with client.websocket_connect("/api/ws") as ada_socket:
        ada_socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
        ada_socket.receive_text()

        with client.websocket_connect("/api/ws") as grace_socket:
            grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
            grace_socket.receive_text()

            send(client, ada, cid, "both sides")

            ada_frame = recv_frame(ada_socket, "message")
            grace_frame = recv_frame(grace_socket, "message")

    assert ada_frame["message"]["body"] == "both sides"
    assert grace_frame["message"]["body"] == "both sides"
    # Same message, opposite ownership flags.
    assert ada_frame["message"]["id"] == grace_frame["message"]["id"]
    assert ada_frame["message"]["mine"] is True
    assert grace_frame["message"]["mine"] is False


def test_websocket_delivers_typing_indicator(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]
    cid = start(client, ada, "grace").json()["id"]

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        grace_socket.receive_text()

        with client.websocket_connect("/api/ws") as ada_socket:
            ada_socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
            ada_socket.receive_text()

            ada_socket.send_text(json.dumps({"type": "typing", "conversation_id": cid}))

            frame = recv_frame(grace_socket, "typing")
            assert frame["conversation_id"] == cid


def test_typing_into_foreign_conversation_is_ignored(client, make_user):
    """A socket must not be able to signal typing into a thread it is not in."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    make_user("linus")
    grace_token = grace["Authorization"].split(" ", 1)[1]
    cid = start(client, ada, "grace").json()["id"]

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        grace_socket.receive_text()

        # Linus is authenticated but is not a participant in this thread.
        linus_token = client.post(
            "/api/auth/login", json={"username": "linus", "password": "password123"}
        ).json()["access_token"]

        with client.websocket_connect("/api/ws") as linus_socket:
            linus_socket.send_text(json.dumps({"type": "auth", "token": linus_token}))
            linus_socket.receive_text()
            linus_socket.send_text(json.dumps({"type": "typing", "conversation_id": cid}))
            # A ping after the ignored frame proves the socket is still alive,
            # and that nothing was relayed into the thread from it.
            linus_socket.send_text(json.dumps({"type": "ping"}))
            assert json.loads(linus_socket.receive_text())["type"] == "pong"

        # Grace's socket must have received no typing frame. The next frame she
        # gets is a real message, which proves the indicator was dropped.
        send(client, ada, cid, "after the attempt")
        assert json.loads(grace_socket.receive_text())["type"] == "message"


def test_websocket_rejects_malformed_first_frame(client):
    with client.websocket_connect("/api/ws") as socket:
        socket.send_text("this is not json")
        try:
            socket.receive_text()
            closed = False
        except Exception:
            closed = True
        assert closed


def _count_selects(client, fn):
    """Run ``fn`` and return ``(result, SELECT statements it issued)``.

    The inbox used to issue a dozen queries per conversation. That is invisible
    against SQLite in a test but costs a network round-trip each against the
    hosted database, so the query count is pinned down here.
    """
    from app.database import get_db

    override = client.app.dependency_overrides[get_db]
    session = next(override())
    engine = session.get_bind()
    session.close()

    statements: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    return result, selects


def test_inbox_reports_partner_preview_and_unread(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")

    cid = start(client, ada, "grace").json()["id"]
    send(client, grace, cid, "hey ada")

    inbox = client.get("/api/conversations", headers=ada).json()
    assert len(inbox) == 1
    entry = inbox[0]
    assert entry["id"] == cid
    assert entry["partner"]["username"] == "grace"
    assert entry["last_message"]["body"] == "hey ada"
    assert entry["last_message"]["mine"] is False
    assert entry["unread_count"] == 1

    # The sender's own copy is theirs, and already read.
    grace_view = client.get("/api/conversations", headers=grace).json()[0]
    assert grace_view["partner"]["username"] == "ada"
    assert grace_view["last_message"]["mine"] is True
    assert grace_view["unread_count"] == 0

    assert client.get("/api/conversations/unread-count", headers=ada).json() == {"count": 1}
    assert client.get("/api/conversations/unread-count", headers=grace).json() == {"count": 0}


def test_inbox_handles_a_thread_with_no_messages(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    entry = client.get("/api/conversations", headers=ada).json()[0]
    assert entry["id"] == cid
    assert entry["last_message"] is None
    assert entry["unread_count"] == 0
    assert entry["partner"]["username"] == "grace"


def test_inbox_query_count_does_not_grow_with_conversations(client, make_user):
    """The inbox costs a fixed number of queries, not one per conversation."""
    ada, _ = make_user("ada")

    for index in range(3):
        peer = f"peer{index}"
        make_user(peer)
        cid = start(client, ada, peer).json()["id"]
        send(client, ada, cid, f"message {index}")

    _, few = _count_selects(client, lambda: client.get("/api/conversations", headers=ada).json())

    for index in range(3, 15):
        peer = f"peer{index}"
        make_user(peer)
        cid = start(client, ada, peer).json()["id"]
        send(client, ada, cid, f"message {index}")

    inbox, many = _count_selects(client, lambda: client.get("/api/conversations", headers=ada).json())

    assert len(inbox) == 15
    # Five times the conversations must not mean five times the queries.
    assert len(many) <= len(few) + 2, (len(few), len(many))


def test_mark_read_clears_the_unread_count(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    send(client, grace, cid, "one")
    send(client, grace, cid, "two")

    assert client.get("/api/conversations/unread-count", headers=ada).json() == {"count": 2}
    client.post(f"/api/conversations/{cid}/read", headers=ada)
    assert client.get("/api/conversations/unread-count", headers=ada).json() == {"count": 0}
    assert client.get("/api/conversations", headers=ada).json()[0]["unread_count"] == 0


# ---------------------------------------------------------------------------
# Replies (swipe-to-reply)
# ---------------------------------------------------------------------------


def test_reply_carries_a_quote_of_the_target(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    original = send(client, ada, cid, "original message").json()
    reply = client.post(
        f"/api/conversations/{cid}/messages",
        headers=grace,
        json={"body": "replying to you", "reply_to_id": original["id"]},
    )
    assert reply.status_code == 201, reply.text
    quote = reply.json()["reply_to"]
    assert quote["id"] == original["id"]
    assert quote["body"] == "original message"
    assert quote["sender_display_name"] == "Ada"
    assert quote["sender_username"] == "ada"
    assert quote["has_media"] is False

    history = client.get(f"/api/conversations/{cid}/messages", headers=grace).json()
    assert history[-1]["reply_to"]["id"] == original["id"]


def test_plain_message_has_no_quote(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    assert send(client, ada, cid, "no reply here").json()["reply_to"] is None


def test_reply_to_a_message_in_another_thread_is_rejected(client, make_user):
    """A quote must not become a way to read a thread you are not part of."""
    ada, _ = make_user("ada")
    make_user("grace")
    make_user("linus")

    with_grace = start(client, ada, "grace").json()["id"]
    with_linus = start(client, ada, "linus").json()["id"]
    secret = send(client, ada, with_linus, "a private aside").json()

    response = client.post(
        f"/api/conversations/{with_grace}/messages",
        headers=ada,
        json={"body": "leaking?", "reply_to_id": secret["id"]},
    )
    assert response.status_code == 400


def test_reply_to_an_unknown_message_is_rejected(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    response = client.post(
        f"/api/conversations/{cid}/messages",
        headers=ada,
        json={"body": "ghost", "reply_to_id": 999999},
    )
    assert response.status_code == 400


def test_reply_is_delivered_over_the_socket_with_its_quote(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    grace_token = grace["Authorization"].split(" ", 1)[1]
    cid = start(client, ada, "grace").json()["id"]
    original = send(client, ada, cid, "hello grace").json()

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        grace_socket.receive_text()

        # The original was sent before the socket opened, so the first frame
        # Grace receives is the reply itself.
        client.post(
            f"/api/conversations/{cid}/messages",
            headers=ada,
            json={"body": "hi back", "reply_to_id": original["id"]},
        )
        frame = json.loads(grace_socket.receive_text())
        assert frame["message"]["body"] == "hi back"
        assert frame["message"]["reply_to"]["id"] == original["id"]


# ---------------------------------------------------------------------------
# Likes on messages (double-tap)
# ---------------------------------------------------------------------------


def like(client, headers, cid, mid):
    return client.post(f"/api/conversations/{cid}/messages/{mid}/like", headers=headers)


def unlike(client, headers, cid, mid):
    return client.delete(f"/api/conversations/{cid}/messages/{mid}/like", headers=headers)


def test_like_and_unlike_a_message(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message = send(client, ada, cid, "like me").json()

    assert like(client, grace, cid, message["id"]).json() == {
        "liked_by_me": True,
        "like_count": 1,
    }

    # Both sides see the total; only the liker sees it as theirs.
    grace_view = client.get(f"/api/conversations/{cid}/messages", headers=grace).json()
    assert grace_view[0]["like_count"] == 1
    assert grace_view[0]["liked_by_me"] is True

    ada_view = client.get(f"/api/conversations/{cid}/messages", headers=ada).json()
    assert ada_view[0]["like_count"] == 1
    assert ada_view[0]["liked_by_me"] is False

    assert unlike(client, grace, cid, message["id"]).json() == {
        "liked_by_me": False,
        "like_count": 0,
    }
    final = client.get(f"/api/conversations/{cid}/messages", headers=grace).json()
    assert final[0]["like_count"] == 0


def test_liking_twice_is_idempotent(client, make_user):
    """A double-tap that races itself must not 500 on the unique constraint."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message = send(client, ada, cid, "double tap").json()

    first = like(client, grace, cid, message["id"])
    second = like(client, grace, cid, message["id"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["like_count"] == 1


def test_unliking_something_not_liked_is_fine(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message = send(client, ada, cid, "never liked").json()
    assert unlike(client, grace, cid, message["id"]).status_code == 200
    assert unlike(client, grace, cid, message["id"]).json()["like_count"] == 0


def test_non_participant_cannot_like_a_message(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    linus, _ = make_user("linus")
    cid = start(client, ada, "grace").json()["id"]
    message = send(client, ada, cid, "private").json()
    assert like(client, linus, cid, message["id"]).status_code == 404
    assert unlike(client, linus, cid, message["id"]).status_code == 404


def test_like_broadcasts_the_new_total_over_the_socket(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    grace_token = grace["Authorization"].split(" ", 1)[1]
    cid = start(client, ada, "grace").json()["id"]
    message = send(client, ada, cid, "watch the count").json()

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        grace_socket.receive_text()

        like(client, ada, cid, message["id"])
        frame = json.loads(grace_socket.receive_text())

    assert frame["type"] == "message_like"
    assert frame["message_id"] == message["id"]
    assert frame["conversation_id"] == cid
    assert frame["like_count"] == 1


# ---------------------------------------------------------------------------
# Last seen
# ---------------------------------------------------------------------------


def test_partner_exposes_last_seen_and_online_state(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    send(client, grace, cid, "hi")

    inbox = client.get("/api/conversations", headers=ada).json()
    partner = inbox[0]["partner"]
    # Grace has made authenticated requests, so her last-seen is set.
    assert partner["last_seen_at"] is not None
    # Neither is holding a socket in this test.
    assert partner["is_online"] is False


def test_presence_is_scoped_to_conversation_partners(client, make_user):
    """Presence is not exposed through ordinary user payloads.

    It is only meaningful in a thread header, so search results, follower lists
    and post authors must not reveal who is online. The conversation partner
    payload is the one place it appears.
    """
    ada, ada_user = make_user("ada")
    grace, grace_user = make_user("grace")
    start(client, ada, "grace")

    # Ada's own profile and the public profile of Grace.
    me = client.get("/api/auth/me", headers=ada).json()
    assert "last_seen_at" not in me or me["last_seen_at"] is None
    profile = client.get("/api/users/grace", headers=ada).json()
    assert "last_seen_at" not in profile or profile["last_seen_at"] is None
    assert profile["is_online"] is False

    search = client.get("/api/search?q=grace", headers=ada).json()
    for user in search.get("users", []):
        assert "last_seen_at" not in user or user["last_seen_at"] is None

    # The partner payload is where it is populated.
    partner = client.get("/api/conversations", headers=ada).json()[0]["partner"]
    assert partner["username"] == "grace"
    assert "last_seen_at" in partner
    assert "is_online" in partner


def test_activity_advances_last_seen(client, make_user):
    """A request after the throttle window moves last_seen_at forward.

    Read through the conversation partner payload, which is where presence is
    exposed; a bare user payload deliberately omits it.
    """
    from app import deps

    ada, _ = make_user("ada")
    make_user("grace")
    start(client, ada, "grace")

    def partner_last_seen() -> str | None:
        inbox = client.get("/api/conversations", headers=ada).json()
        return inbox[0]["partner"]["last_seen_at"]

    first = partner_last_seen()
    assert first is not None

    # Inside the window nothing is written, so the value is unchanged.
    assert partner_last_seen() == first

    # Pretend the window has passed.
    deps._LAST_SEEN_WRITTEN.clear()
    later = partner_last_seen()
    assert later is not None and later >= first


def test_socket_close_stamps_last_seen(client, make_user):
    """Closing a socket records the moment presence ended."""
    from app.database import get_db
    from app.models import User

    ada, ada_user = make_user("ada")
    token = ada["Authorization"].split(" ", 1)[1]

    with client.websocket_connect("/api/ws") as socket:
        socket.send_text(json.dumps({"type": "auth", "token": token}))
        socket.receive_text()

    override = client.app.dependency_overrides[get_db]
    session = next(override())
    try:
        user = session.get(User, ada_user["id"])
        session.refresh(user)
        assert user.last_seen_at is not None
    finally:
        session.close()


def test_partner_presence_is_announced_to_the_conversation(client, make_user):
    """A partner coming online and going offline is pushed to the other member.

    Presence is only rendered in a thread header, so the frame must reach a
    conversation partner — and it must carry the freshly stamped
    ``last_seen_at`` on the way out, so the header can show a time without a
    follow-up request.
    """
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]
    start(client, ada, "grace")

    # Ada is already connected when Grace arrives, so she sees the transition.
    with client.websocket_connect("/api/ws") as ada_socket:
        ada_socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
        ada_socket.receive_text()

        with client.websocket_connect("/api/ws") as grace_socket:
            grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
            grace_socket.receive_text()

            online = recv_frame(ada_socket, "presence")
            assert online["user_id"] == 2
            assert online["is_online"] is True

        offline = recv_frame(ada_socket, "presence")
        assert offline["user_id"] == 2
        assert offline["is_online"] is False
        assert offline["last_seen_at"] is not None


def test_presence_frame_timestamp_carries_a_utc_offset(client, make_user):
    """The "last seen" frame must not hand the browser a bare local time.

    SQLite returns naive datetimes, and the frame used to be serialized with
    `default=str`, which produced "2026-11-22 19:29:29" with no offset. A
    browser parses an offsetless timestamp as *local* time, so a partner seen a
    minute ago was rendered as "1h ago" for anyone east of UTC — the reported
    "we just chatted but it says an hour ago" bug.

    The API already guarantees this through `UTCDatetime`; frames must agree.
    """
    from datetime import datetime, timezone

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]
    start(client, ada, "grace")

    with client.websocket_connect("/api/ws") as ada_socket:
        ada_socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
        ada_socket.receive_text()
        with client.websocket_connect("/api/ws") as grace_socket:
            grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
            grace_socket.receive_text()

        # Grace connecting announces her online first, then offline on close.
        online = recv_frame(ada_socket, "presence")
        assert online["is_online"] is True
        offline = recv_frame(ada_socket, "presence")
        assert offline["is_online"] is False

    stamp = offline["last_seen_at"]
    assert stamp is not None
    # An ISO-8601 string the browser can parse unambiguously: either a "Z" or a
    # "+00:00" offset. A bare "YYYY-MM-DD HH:MM:SS" is the bug.
    assert "T" in stamp, f"expected an ISO timestamp, got {stamp!r}"
    assert stamp.endswith("Z") or stamp.endswith("+00:00"), stamp
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    # And it must be the real moment, not hours off.
    age = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert age < 120, f"last seen was {age:.0f}s away from now"


def test_socket_connect_does_not_query_the_database(client, make_user):
    """Opening a socket must not cost a database round trip.

    The ring replay used to read the call log on every connect. On production
    that blocked the event loop for ~1.3s per connect and stretched ordinary
    reads from 2.3s to 7.8s, which users saw as slow sends, late message
    rendering and media that appeared to reload. The replay is answered from
    the in-memory buffer, so a connect should issue no statements at all.
    """
    from sqlalchemy import event as sa_event

    from app.database import get_db

    ada, _ = make_user("ada")
    token = ada["Authorization"].split(" ", 1)[1]

    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    override = client.app.dependency_overrides[get_db]
    session = next(override())
    engine = session.get_bind()
    session.close()

    sa_event.listen(engine, "before_cursor_execute", count)
    try:
        with client.websocket_connect("/api/ws") as socket:
            socket.send_text(json.dumps({"type": "auth", "token": token}))
            assert json.loads(socket.receive_text())["type"] == "ready"
            socket.send_text(json.dumps({"type": "ping"}))
            assert json.loads(socket.receive_text())["type"] == "pong"
    finally:
        sa_event.remove(engine, "before_cursor_execute", count)

    # Authenticating and announcing presence legitimately read the user row and
    # the participant list. The call log must not be read: that is the query
    # this test exists to keep off the connect path.
    call_log_reads = [sql for sql in statements if "FROM messages" in sql]
    assert call_log_reads == [], f"connect read the call log: {call_log_reads}"


def _user_id(headers, client):
    """The authenticated user's id, read back from the API."""
    return client.get("/api/auth/me", headers=headers).json()["id"]


def test_presence_is_not_announced_for_strangers(client, make_user, monkeypatch):
    """A user with no shared conversation is not told about the transition.

    Presence fan-out is scoped to conversation partners, so a stranger coming
    online must not generate a frame at all. Capturing the published peers is
    more precise than reading a socket, which cannot distinguish "no frame
    because out of scope" from "no frame yet".
    """
    from app import realtime as realtime_module

    ada, _ = make_user("ada")
    make_user("grace")
    linus, linus_user = make_user("linus")
    grace_token = client.post(
        "/api/auth/login", json={"username": "grace", "password": "password123"}
    ).json()["access_token"]
    # Ada and Grace share a thread; Linus is in neither conversation.
    start(client, ada, "grace")

    published: list[tuple[int, dict]] = []
    real_publish = realtime_module.manager.publish

    async def record(user_id: int, payload: dict) -> None:
        published.append((user_id, payload))
        await real_publish(user_id, payload)

    monkeypatch.setattr(realtime_module.manager, "publish", record)

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        grace_socket.receive_text()

    presence = [(uid, p) for uid, p in published if p.get("type") == "presence"]
    assert presence, "the online transition was never announced"
    recipients = {uid for uid, _ in presence}
    assert recipients == {_user_id(ada, client)}, recipients
    assert linus_user["id"] not in recipients


def test_second_socket_does_not_reannounce_presence(client, make_user, monkeypatch):
    """Presence is a transition: a second tab must not flip the status again."""
    from app import realtime as realtime_module

    ada, _ = make_user("ada")
    grace, grace_user = make_user("grace")
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]
    start(client, ada, "grace")

    published: list[dict] = []
    real_publish = realtime_module.manager.publish

    async def record(user_id: int, payload: dict) -> None:
        if payload.get("type") == "presence":
            published.append(payload)
        await real_publish(user_id, payload)

    monkeypatch.setattr(realtime_module.manager, "publish", record)

    with client.websocket_connect("/api/ws") as ada_socket:
        ada_socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
        ada_socket.receive_text()

        # Two tabs for Grace. Only the first is a transition.
        with client.websocket_connect("/api/ws") as grace_one:
            grace_one.send_text(json.dumps({"type": "auth", "token": grace_token}))
            grace_one.receive_text()
            assert recv_frame(ada_socket, "presence")["is_online"] is True

            with client.websocket_connect("/api/ws") as grace_two:
                grace_two.send_text(json.dumps({"type": "auth", "token": grace_token}))
                grace_two.receive_text()
            # One tab down, but Grace still has a socket open.

        # Both tabs are now closed, which is the one offline transition.
        assert recv_frame(ada_socket, "presence")["is_online"] is False

    grace_events = [p["is_online"] for p in published if p["user_id"] == grace_user["id"]]
    # One online for the first tab, one offline when the last tab closed. The
    # second tab opening and closing added nothing.
    assert grace_events == [True, False], grace_events


def send_sticker(client, headers, conversation_id: int, sticker: str, **extra):
    return client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={"sticker": sticker, **extra},
    )


def test_sticker_catalogue_is_served_to_signed_in_users(client, make_user):
    ada, _ = make_user("ada")
    response = client.get("/api/conversations/stickers", headers=ada)
    assert response.status_code == 200, response.text
    catalogue = response.json()
    assert len(catalogue) >= 20
    ids = [item["id"] for item in catalogue]
    assert len(ids) == len(set(ids)), "sticker ids must be unique"
    assert "heart" in ids
    assert all(item["emoji"] and item["label"] and item["pack"] for item in catalogue)


def test_sticker_catalogue_requires_authentication(client):
    assert client.get("/api/conversations/stickers").status_code == 401


def test_send_sticker_without_body(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    response = send_sticker(client, ada, cid, "heart")
    assert response.status_code == 201, response.text
    message = response.json()
    assert message["sticker"] == "heart"
    assert message["body"] == ""
    assert message["mine"] is True

    # The recipient sees the same sticker.
    history = client.get(f"/api/conversations/{cid}/messages", headers=grace).json()
    assert history[-1]["sticker"] == "heart"


def test_sticker_shows_in_inbox_preview(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    send_sticker(client, ada, cid, "party")

    inbox = client.get("/api/conversations", headers=ada).json()
    assert inbox[0]["last_message"]["sticker"] == "party"


def test_unknown_sticker_is_rejected(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    response = send_sticker(client, ada, cid, "not-a-real-sticker")
    assert response.status_code == 400
    assert "sticker" in response.json()["detail"].lower()


def test_sticker_with_caption_keeps_both(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    response = send_sticker(client, ada, cid, "rocket", body="shipping today")
    assert response.status_code == 201, response.text
    message = response.json()
    assert message["sticker"] == "rocket"
    assert message["body"] == "shipping today"


def test_plain_message_has_no_sticker(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    response = send(client, ada, cid, "just text")
    assert response.status_code == 201, response.text
    assert response.json()["sticker"] is None


def test_sticker_is_broadcast_over_the_socket(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    grace_token = grace["Authorization"].split(" ", 1)[1]
    with client.websocket_connect("/api/ws") as socket:
        socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        recv_frame(socket, "ready")
        send_sticker(client, ada, cid, "fire")
        frame = recv_frame(socket, "message")
        assert frame["message"]["sticker"] == "fire"


# ---------------------------------------------------------------------------
# Edit, delete and forward
# ---------------------------------------------------------------------------


def test_author_can_edit_a_message(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, cid, "tpyo").json()["id"]

    response = client.patch(
        f"/api/conversations/{cid}/messages/{message_id}",
        headers=ada,
        json={"body": "typo"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["body"] == "typo"
    assert response.json()["edited_at"] is not None

    history = client.get(f"/api/conversations/{cid}/messages", headers=grace).json()
    assert history[0]["body"] == "typo"
    assert history[0]["edited_at"] is not None


def test_recipient_cannot_edit_someone_elses_message(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, cid, "mine").json()["id"]

    response = client.patch(
        f"/api/conversations/{cid}/messages/{message_id}",
        headers=grace,
        json={"body": "theirs now"},
    )
    assert response.status_code == 403


def test_edit_is_delivered_over_the_socket(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, cid, "before").json()["id"]

    grace_token = grace["Authorization"].split(" ", 1)[1]
    with client.websocket_connect("/api/ws") as socket:
        socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        recv_frame(socket, "ready")
        client.patch(
            f"/api/conversations/{cid}/messages/{message_id}",
            headers=ada,
            json={"body": "after"},
        )
        frame = recv_frame(socket, "message_edit")
        assert frame["message"]["body"] == "after"


def test_author_can_delete_a_message(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, cid, "regrettable").json()["id"]

    response = client.delete(f"/api/conversations/{cid}/messages/{message_id}", headers=ada)
    assert response.status_code == 204

    assert client.get(f"/api/conversations/{cid}/messages", headers=grace).json() == []


def test_delete_clears_the_quote_on_a_reply(client, make_user):
    """A reply outlives the message it quoted, without the quote."""
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    original = send(client, ada, cid, "original").json()["id"]
    reply = client.post(
        f"/api/conversations/{cid}/messages",
        headers=ada,
        json={"body": "a reply", "reply_to_id": original},
    ).json()["id"]

    assert client.delete(f"/api/conversations/{cid}/messages/{original}", headers=ada).status_code == 204

    history = client.get(f"/api/conversations/{cid}/messages", headers=ada).json()
    assert [m["id"] for m in history] == [reply]
    assert history[0]["reply_to"] is None


def test_delete_is_delivered_over_the_socket(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, cid, "gone soon").json()["id"]

    grace_token = grace["Authorization"].split(" ", 1)[1]
    with client.websocket_connect("/api/ws") as socket:
        socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        recv_frame(socket, "ready")
        client.delete(f"/api/conversations/{cid}/messages/{message_id}", headers=ada)
        frame = recv_frame(socket, "message_delete")
        assert frame["message_id"] == message_id


def test_recipient_cannot_delete_someone_elses_message(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, cid, "keep me").json()["id"]

    assert client.delete(f"/api/conversations/{cid}/messages/{message_id}", headers=grace).status_code == 403


def test_forward_copies_content_into_another_thread(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    make_user("linus")
    first = start(client, ada, "grace").json()["id"]
    second = start(client, ada, "linus").json()["id"]
    message_id = send(client, ada, first, "worth sharing").json()["id"]

    response = client.post(
        f"/api/conversations/{first}/messages/{message_id}/forward",
        headers=ada,
        params={"target_conversation_id": second},
    )
    assert response.status_code == 201, response.text
    forwarded = response.json()
    assert forwarded["body"] == "worth sharing"
    assert forwarded["forwarded"] is True
    assert forwarded["conversation_id"] == second

    # The original thread keeps its own copy of the message.
    history = client.get(f"/api/conversations/{first}/messages", headers=ada).json()
    assert [m["body"] for m in history] == ["worth sharing"]


def test_cannot_forward_into_a_thread_you_are_not_in(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    make_user("linus")
    make_user("margaret")
    mine = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, mine, "private").json()["id"]

    foreign = start(client, make_user("donald")[0], "margaret").json()["id"]
    response = client.post(
        f"/api/conversations/{mine}/messages/{message_id}/forward",
        headers=ada,
        params={"target_conversation_id": foreign},
    )
    assert response.status_code == 404


def test_cannot_forward_a_message_you_cannot_read(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    linus, _ = make_user("linus")
    make_user("margaret")
    ada_thread = start(client, ada, "grace").json()["id"]
    message_id = send(client, ada, ada_thread, "not for you").json()["id"]

    linus_thread = start(client, linus, "margaret").json()["id"]
    response = client.post(
        f"/api/conversations/{ada_thread}/messages/{message_id}/forward",
        headers=linus,
        params={"target_conversation_id": linus_thread},
    )
    assert response.status_code == 404


def test_forward_by_id_on_send_copies_the_body(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    make_user("linus")
    first = start(client, ada, "grace").json()["id"]
    second = start(client, ada, "linus").json()["id"]
    message_id = send(client, ada, first, "forward me").json()["id"]

    response = client.post(
        f"/api/conversations/{second}/messages",
        headers=ada,
        json={"forwarded_from_id": message_id},
    )
    assert response.status_code == 201, response.text
    assert response.json()["body"] == "forward me"
    assert response.json()["forwarded"] is True


# ---------------------------------------------------------------------------
# Read receipts
# ---------------------------------------------------------------------------


def test_read_receipt_marks_the_senders_messages_seen(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    send(client, ada, cid, "did you see this?")

    mine = client.get(f"/api/conversations/{cid}/messages", headers=ada).json()
    assert mine[0]["read_by_partner"] is False

    client.post(f"/api/conversations/{cid}/read", headers=grace)

    mine = client.get(f"/api/conversations/{cid}/messages", headers=ada).json()
    assert mine[0]["read_by_partner"] is True

    inbox = client.get("/api/conversations", headers=ada).json()
    assert inbox[0]["partner_read_at"] is not None


def test_read_receipt_reaches_the_sender_over_the_socket(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    send(client, ada, cid, "ping")

    ada_token = ada["Authorization"].split(" ", 1)[1]
    with client.websocket_connect("/api/ws") as socket:
        socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
        recv_frame(socket, "ready")
        client.post(f"/api/conversations/{cid}/read", headers=grace)
        frame = recv_frame(socket, "read")
        assert frame["conversation_id"] == cid
        assert frame["last_read_at"] is not None


def test_inbox_carries_partner_read_state(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    send(client, grace, cid, "hello")

    inbox = client.get("/api/conversations", headers=ada).json()
    assert inbox[0]["unread_count"] == 1
    assert inbox[0]["partner_read_at"] is not None


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


def test_call_frames_reach_the_other_participant(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        recv_frame(grace_socket, "ready")

        with client.websocket_connect("/api/ws") as ada_socket:
            ada_socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
            recv_frame(ada_socket, "ready")

            ada_socket.send_text(
                json.dumps(
                    {
                        "type": "call",
                        "conversation_id": cid,
                        "action": "offer",
                        "call_id": "abc",
                        "mode": "video",
                        "payload": "v=0",
                    }
                )
            )
            frame = recv_frame(grace_socket, "call")
            assert frame["action"] == "offer"
            assert frame["mode"] == "video"
            assert frame["payload"] == "v=0"
            assert frame["from"] != 0


def test_call_frames_into_a_foreign_thread_are_dropped(client, make_user):
    """A socket must not be able to ring somebody it shares no thread with."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    make_user("linus")
    cid = start(client, ada, "grace").json()["id"]
    grace_token = grace["Authorization"].split(" ", 1)[1]

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        recv_frame(grace_socket, "ready")
        # Grace is not a participant of this conversation id at all.
        grace_socket.send_text(
            json.dumps({"type": "call", "conversation_id": cid + 999, "action": "offer"})
        )
        grace_socket.send_text(json.dumps({"type": "ping"}))
        # The next frame is the pong, proving no call was relayed ahead of it.
        assert json.loads(grace_socket.receive_text())["type"] == "pong"


def test_unknown_call_action_is_ignored(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    grace_token = grace["Authorization"].split(" ", 1)[1]

    with client.websocket_connect("/api/ws") as grace_socket:
        grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
        recv_frame(grace_socket, "ready")
        grace_socket.send_text(
            json.dumps({"type": "call", "conversation_id": cid, "action": "exfiltrate"})
        )
        grace_socket.send_text(json.dumps({"type": "ping"}))
        assert json.loads(grace_socket.receive_text())["type"] == "pong"


def test_sticker_catalogue_ships_several_packs(client, make_user):
    ada, _ = make_user("ada")
    catalogue = client.get("/api/conversations/stickers", headers=ada).json()
    packs = {entry["pack"] for entry in catalogue}
    assert {"Reactions", "Moods", "Lifestyle", "Celebration", "Animals"} <= packs
    assert len(catalogue) > 60


# --- Call log in the thread -------------------------------------------------
#
# A call is recorded as a message row so it sorts into the history. These tests
# drive the real relay frames over a socket, because the state transitions are
# decided by which side sends which frame — calling the recorder directly would
# not prove that.


def _call(client_socket, token: str, conversation_id: int, action: str, call_id: str = "c1",
          mode: str = "audio", payload: str | None = None) -> None:
    frame = {
        "type": "call",
        "conversation_id": conversation_id,
        "action": action,
        "call_id": call_id,
        "mode": mode,
    }
    if payload is not None:
        frame["payload"] = payload
    client_socket.send_text(json.dumps(frame))


def _history(client, headers, conversation_id: int) -> list[dict]:
    response = client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
    assert response.status_code == 200
    return response.json()


def _call_entries(client, headers, conversation_id: int) -> list[dict]:
    return [message for message in _history(client, headers, conversation_id) if message["call"]]


def _two_sockets(client, ada, grace):
    """Both participants connected, in the order the relay expects."""
    ada_token = ada["Authorization"].split(" ", 1)[1]
    grace_token = grace["Authorization"].split(" ", 1)[1]
    grace_socket = client.websocket_connect("/api/ws")
    grace_socket.__enter__()
    grace_socket.send_text(json.dumps({"type": "auth", "token": grace_token}))
    recv_frame(grace_socket, "ready")
    ada_socket = client.websocket_connect("/api/ws")
    ada_socket.__enter__()
    ada_socket.send_text(json.dumps({"type": "auth", "token": ada_token}))
    recv_frame(ada_socket, "ready")
    return ada_socket, grace_socket


def _close(socket) -> None:
    socket.__exit__(None, None, None)


def test_offer_records_a_call_entry_both_sides_can_read(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer", mode="video")
        # The entry is published before the signalling frame, so the first
        # `call` frame arrives after the entry has already been recorded.
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    for headers in (ada, grace):
        entries = _call_entries(client, headers, cid)
        assert len(entries) == 1
        call = entries[0]["call"]
        assert call["mode"] == "video"
        assert call["state"] == "ringing"
        assert call["seconds"] is None

    # The direction is relative to the reader, not stored on the row.
    assert _call_entries(client, ada, cid)[0]["call"]["by_me"] is True
    assert _call_entries(client, grace, cid)[0]["call"]["by_me"] is False


def test_answered_call_records_its_duration(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(grace_socket, "grace", cid, "answer", payload="v=0")
        recv_frame(ada_socket, "call")
        # The duration comes from when media connected, so the test rewinds the
        # stored stamp rather than sleeping.
        _call(ada_socket, "ada", cid, "hangup")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    entries = _call_entries(client, ada, cid)
    assert len(entries) == 1
    call = entries[0]["call"]
    assert call["state"] == "answered"
    # Answering and hanging up happen in the same instant here, so the talk time
    # is zero seconds rather than a missing duration: the call did connect.
    assert call["seconds"] == 0


def test_a_call_nobody_answers_is_missed_for_the_callee(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        # The caller gives up without the callee ever answering.
        _call(ada_socket, "ada", cid, "hangup")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    # The same row reads as two different events, which is the whole reason the
    # state is resolved per reader rather than stored as one label.
    assert _call_entries(client, ada, cid)[0]["call"]["state"] == "cancelled"
    assert _call_entries(client, grace, cid)[0]["call"]["state"] == "missed"
    assert _call_entries(client, grace, cid)[0]["call"]["seconds"] is None


def test_a_declined_call_is_recorded_as_declined(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(grace_socket, "grace", cid, "decline")
        recv_frame(ada_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    for headers in (ada, grace):
        entries = _call_entries(client, headers, cid)
        assert len(entries) == 1
        assert entries[0]["call"]["state"] == "declined"
        assert entries[0]["call"]["seconds"] is None


def test_one_call_leaves_one_entry_however_many_frames_arrive(client, make_user):
    """A reconnecting peer re-sends the offer; that must not duplicate the row."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(grace_socket, "grace", cid, "answer")
        recv_frame(ada_socket, "call")
        _call(ada_socket, "ada", cid, "hangup")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    entries = _call_entries(client, ada, cid)
    assert len(entries) == 1
    assert entries[0]["call"]["state"] == "answered"


def test_a_call_entry_cannot_be_edited_or_forwarded(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]
    other = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    entry = _call_entries(client, ada, cid)[0]
    edited = client.patch(
        f"/api/conversations/{cid}/messages/{entry['id']}",
        headers=ada,
        json={"body": "not a call"},
    )
    assert edited.status_code == 400

    forwarded = client.post(
        f"/api/conversations/{cid}/messages/{entry['id']}/forward",
        headers=ada,
        params={"target_conversation_id": other},
    )
    assert forwarded.status_code == 400


def test_a_call_entry_names_itself_when_quoted(client, make_user):
    """A reply to a call must not quote an empty line."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer", mode="video")
        recv_frame(grace_socket, "call")
        _call(ada_socket, "ada", cid, "hangup")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    entry = _call_entries(client, ada, cid)[0]
    reply = client.post(
        f"/api/conversations/{cid}/messages",
        headers=grace,
        json={"body": "sorry I missed it", "reply_to_id": entry["id"]},
    )
    assert reply.status_code == 201
    # Worded for whoever is reading the reply, the same way the entry is: the
    # call reads as missed to Grace, who was the one being called.
    assert reply.json()["reply_to"]["body"] == "Video call (missed)"
    for_viewer = client.get(f"/api/conversations/{cid}/messages", headers=ada).json()
    quoted = next(m for m in for_viewer if m["body"] == "sorry I missed it")
    assert quoted["reply_to"]["body"] == "Video call"


def test_the_inbox_preview_names_a_call(client, make_user):
    """The newest message drives the inbox row, and a call has no body."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(grace_socket, "grace", cid, "decline")
        recv_frame(ada_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    inbox = client.get("/api/conversations", headers=grace).json()
    row = next(item for item in inbox if item["id"] == cid)
    assert row["last_message"]["call"]["state"] == "declined"


def test_a_call_row_needs_no_sticker_to_serialize(client, make_user):
    """A call row has no body and no sticker; it must still be a valid message."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    entry = _call_entries(client, ada, cid)[0]
    assert entry["body"] == ""
    assert entry["sticker"] is None
    assert entry["media"] == []


# --- Sticker catalogue ------------------------------------------------------


def test_every_sticker_pack_is_published(client, make_user):
    ada, _ = make_user("ada")
    catalogue = client.get("/api/conversations/stickers", headers=ada).json()
    packs = {sticker["pack"] for sticker in catalogue}
    assert {"Reactions", "Greetings", "Flags"} <= packs
    assert len(packs) >= 9


def test_the_flag_pack_covers_every_country(client, make_user):
    """The pack is generated from ISO 3166-1, so its size is the check."""
    from app.flags import COUNTRIES

    ada, _ = make_user("ada")
    catalogue = client.get("/api/conversations/stickers", headers=ada).json()
    flags = [sticker for sticker in catalogue if sticker["pack"] == "Flags"]
    assert len(flags) == len(COUNTRIES) == 249
    assert len({sticker["id"] for sticker in flags}) == 249
    # Each entry is a two-letter regional-indicator pair, not a letter fallback.
    for sticker in flags:
        assert len(sticker["emoji"]) == 2
        assert all(0x1F1E6 <= ord(char) <= 0x1F1FF for char in sticker["emoji"])


def test_a_flag_can_be_sent_and_read_back(client, make_user):
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    sent = client.post(
        f"/api/conversations/{cid}/messages",
        headers=ada,
        json={"body": "", "sticker": "flag-ng"},
    )
    assert sent.status_code == 201
    assert sent.json()["sticker"] == "flag-ng"

    history = client.get(f"/api/conversations/{cid}/messages", headers=ada).json()
    assert history[-1]["sticker"] == "flag-ng"


def test_an_unknown_flag_id_is_rejected(client, make_user):
    """A code that is not a country must not become a sticker."""
    ada, _ = make_user("ada")
    make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    sent = client.post(
        f"/api/conversations/{cid}/messages",
        headers=ada,
        json={"body": "", "sticker": "flag-zz"},
    )
    assert sent.status_code == 400


def test_a_missed_call_notifies_the_person_who_did_not_answer(client, make_user, monkeypatch):
    """A missed call has to reach a closed tab, which is where it matters."""
    calls: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        "app.routers.realtime.push.schedule_notify",
        lambda user_id, payload: calls.append((user_id, payload)) or True,
    )

    ada, ada_user = make_user("ada")
    grace, grace_user = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(ada_socket, "ada", cid, "hangup")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    recipients = [user_id for user_id, _ in calls]
    assert grace_user["id"] in recipients
    # The caller is looking at their own screen; alerting them is noise.
    assert ada_user["id"] not in recipients
    missed = next(payload for user_id, payload in calls if user_id == grace_user["id"])
    # Worded for the recipient: the caller cancelled, but what happened to Grace
    # is that she missed it.
    assert missed["body"] == "Missed voice call"


def test_an_answered_call_does_not_notify(client, make_user, monkeypatch):
    """Only a call that was not picked up is worth an alert."""
    calls: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        "app.routers.realtime.push.schedule_notify",
        lambda user_id, payload: calls.append((user_id, payload)) or True,
    )

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(grace_socket, "grace", cid, "answer")
        recv_frame(ada_socket, "call")
        _call(ada_socket, "ada", cid, "hangup")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    assert calls == []


# --- Calls that never report their end --------------------------------------
#
# The frame that ends a call travels over the same socket as everything else, so
# it is lost whenever the peer closes the tab, drops the network, or the process
# restarts mid-call. The row is then left `ringing` and both readers see a call
# that is still in progress. These tests cover the sweeper that finalises those
# rows and, crucially, publishes the corrected entry so an open thread updates
# without a reload.


def _pushed_call_state(socket, expected: str) -> dict:
    """Read pushed message frames until the call entry reports ``expected``."""
    for _ in range(10):
        frame = recv_frame(socket, "message")
        call = frame["message"].get("call")
        if call and call["state"] == expected:
            return frame
    raise AssertionError(f"no pushed call entry in state {expected!r}")


def test_a_ringing_call_is_finalised_and_pushed_to_both_sides(client, make_user, db_session):
    """An abandoned call stops reading as live, live, on both screens."""
    from datetime import timedelta

    from app.models import Message, utcnow
    from app.routers.realtime import CALL_RING_STALE_SECONDS, sweep_stale_calls

    ada, ada_user = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        # The callee's tab is closed, so the decline never arrives. The row is
        # aged past the ring window rather than waiting 90 real seconds.
        row = db_session.query(Message).filter(Message.call_ref == "c1").one()
        row.created_at = utcnow() - timedelta(seconds=CALL_RING_STALE_SECONDS + 5)
        db_session.commit()

        resolved = asyncio.run(sweep_stale_calls(db_session))
        assert resolved == 1

        # Both peers are told, and the pushed entry is the finished one: this is
        # what makes the open thread update rather than wait for a reload. The
        # ringing entry is still queued from the offer, so the frame that
        # matters is the later one for the same row.
        for socket, expected in ((ada_socket, "cancelled"), (grace_socket, "missed")):
            pushed = _pushed_call_state(socket, expected)
            assert pushed["message"]["call"]["state"] == expected
    finally:
        _close(ada_socket)
        _close(grace_socket)

    assert _call_entries(client, ada, cid)[0]["call"]["state"] == "cancelled"
    assert _call_entries(client, grace, cid)[0]["call"]["state"] == "missed"


def test_a_call_that_connected_long_ago_is_finalised_with_its_duration(
    client, make_user, db_session
):
    """A hangup lost after media connected must not read as an endless call."""
    from datetime import timedelta

    from app.models import Message, utcnow
    from app.routers.realtime import CALL_ACTIVE_STALE_SECONDS, sweep_stale_calls

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(grace_socket, "grace", cid, "answer")
        recv_frame(ada_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    row = db_session.query(Message).filter(Message.call_ref == "c1").one()
    row.created_at = utcnow() - timedelta(seconds=CALL_ACTIVE_STALE_SECONDS + 60)
    row.call_connected_at = utcnow() - timedelta(seconds=125)
    db_session.commit()

    assert asyncio.run(sweep_stale_calls(db_session)) == 1

    entry = _call_entries(client, ada, cid)[0]["call"]
    assert entry["state"] == "answered"
    # The duration is the talk time, not the age of the row.
    assert entry["seconds"] is not None
    assert entry["seconds"] >= 120


def test_the_sweeper_leaves_a_call_that_is_still_ringing_alone(client, make_user, db_session):
    """A call in progress must not be finalised out from under the two peers."""
    from app.models import Message
    from app.routers.realtime import sweep_stale_calls

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        assert asyncio.run(sweep_stale_calls(db_session)) == 0
    finally:
        _close(ada_socket)
        _close(grace_socket)

    row = db_session.query(Message).filter(Message.call_ref == "c1").one()
    assert row.call_state == "ringing"
    assert _call_entries(client, grace, cid)[0]["call"]["state"] == "ringing"


def test_the_sweeper_runs_its_queries_off_the_event_loop():
    """A sweep must not block the loop that answers every other request.

    The sweeper used to call SQLAlchemy straight from a background task, so each
    pass occupied the event loop for the length of several queries. Against a
    hosted database that is long enough for the platform's health check to time
    out, which is what restarted the service mid-call.

    Guarded structurally rather than by timing a real query: a blocking call
    would have to appear inside the coroutine itself, and this fails if one does.
    """
    import inspect as _inspect

    from app.routers import realtime

    source = _inspect.getsource(realtime.sweep_stale_calls)
    # The coroutine must hand the blocking work to a worker thread...
    assert "to_thread" in source
    # ...rather than doing it itself, which would stall the loop.
    for blocking in ("db.scalars", "db.commit", "db.get("):
        assert blocking not in source
    # And the function it delegates to must not be a coroutine, or `to_thread`
    # would return an un-awaited coroutine instead of a result.
    assert not _inspect.iscoroutinefunction(realtime._sweep_stale_calls_sync)


def test_the_sweeper_still_finalises_a_stale_row_through_its_threaded_path(
    client, make_user, db_session
):
    """The threaded path must return the count and still push the finished entry."""
    from datetime import timedelta

    from app.models import Message, utcnow
    from app.routers.realtime import CALL_RING_STALE_SECONDS, sweep_stale_calls

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        row = db_session.query(Message).filter(Message.call_ref == "c1").one()
        row.created_at = utcnow() - timedelta(seconds=CALL_RING_STALE_SECONDS + 5)
        db_session.commit()

        assert asyncio.run(sweep_stale_calls(db_session)) == 1
        pushed = _pushed_call_state(ada_socket, "cancelled")
        assert pushed["message"]["call"]["state"] == "cancelled"
    finally:
        _close(ada_socket)
        _close(grace_socket)


def test_a_call_still_marked_live_at_boot_is_closed(client, make_user, db_session):
    """A restart ends every call, so nothing may read as live after one.

    The process that was relaying a call cannot outlive it: its sockets drop and
    the hangup frame that would close the row is never sent. Before this the row
    stayed `active` until the six-hour backstop, which showed as a call that had
    ended still reading as ongoing in the thread and the inbox.
    """
    from app.main import _close_calls_left_open_by_a_restart
    from app.models import Message

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
        _call(grace_socket, "grace", cid, "answer")
        recv_frame(ada_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    # The answer was recorded, so the row is `active` when the process dies.
    row = db_session.query(Message).filter(Message.call_ref == "c1").one()
    assert row.call_state == "active"

    _close_calls_left_open_by_a_restart(db_session)

    db_session.expire_all()
    row = db_session.query(Message).filter(Message.call_ref == "c1").one()
    assert row.call_state == "answered"
    assert row.call_seconds is not None
    assert _call_entries(client, ada, cid)[0]["call"]["state"] == "answered"


def test_a_ringing_row_left_at_boot_reads_as_missed(client, make_user, db_session):
    """A call that was still ringing when the process died was never answered."""
    from app.main import _close_calls_left_open_by_a_restart
    from app.models import Message

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    ada_socket, grace_socket = _two_sockets(client, ada, grace)
    try:
        _call(ada_socket, "ada", cid, "offer")
        recv_frame(grace_socket, "call")
    finally:
        _close(ada_socket)
        _close(grace_socket)

    _close_calls_left_open_by_a_restart(db_session)

    db_session.expire_all()
    assert _call_entries(client, ada, cid)[0]["call"]["state"] == "cancelled"
    assert _call_entries(client, grace, cid)[0]["call"]["state"] == "missed"


def test_a_burst_of_call_frames_does_not_query_the_database_each_time(
    client, make_user, db_session
):
    """A call's frames must not each pay a membership query.

    Trickle ICE sends a dozen frames per call, and typing fires per keystroke.
    Every one of those used to run a membership lookup on the event loop, which
    is what made call setup crawl on a hosted database.
    """
    from sqlalchemy import event as sa_event

    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    cid = start(client, ada, "grace").json()["id"]

    queries = 0

    def count(conn, cursor, statement, params, context, executemany):
        nonlocal queries
        # Only the peer lookup the relay path makes, not the unrelated
        # participant queries the HTTP endpoints run around it.
        flat = " ".join(statement.split())
        if flat.startswith("SELECT conversation_participants.user_id FROM") and (
            "conversation_id = ?" in flat
        ):
            queries += 1

    # The engine the API is actually writing to in this test, not the app's
    # configured one, which the fixture has overridden.
    test_engine = db_session.get_bind()

    sa_event.listen(test_engine, "before_cursor_execute", count)
    try:
        ada_socket, grace_socket = _two_sockets(client, ada, grace)
        try:
            _call(ada_socket, "ada", cid, "offer")
            recv_frame(grace_socket, "call")
            # A burst of ICE frames, as a real call sends.
            for i in range(12):
                _call(ada_socket, "ada", cid, "ice", payload=f'{{"candidate":{i}}}')
            for _ in range(12):
                recv_frame(grace_socket, "call")
        finally:
            _close(ada_socket)
            _close(grace_socket)
    finally:
        sa_event.remove(test_engine, "before_cursor_execute", count)

    # One lookup per socket for the whole burst; without the cache this is one
    # per frame, so 26 rather than 2.
    assert queries <= 2, f"expected the peer list to be cached, saw {queries} queries"
