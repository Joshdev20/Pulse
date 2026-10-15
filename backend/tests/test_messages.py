"""Tests for direct messages, conversations and the realtime socket."""

import json


def start(client, headers, username: str):
    return client.post("/api/conversations", headers=headers, json={"username": username})


def send(client, headers, conversation_id: int, body: str):
    return client.post(
        f"/api/conversations/{conversation_id}/messages", headers=headers, json={"body": body}
    )


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

            ada_frame = json.loads(ada_socket.receive_text())
            grace_frame = json.loads(grace_socket.receive_text())

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

            frame = json.loads(grace_socket.receive_text())
            assert frame["type"] == "typing"
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
