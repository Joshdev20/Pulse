"""Tests for direct messages, conversations and the realtime socket."""

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
