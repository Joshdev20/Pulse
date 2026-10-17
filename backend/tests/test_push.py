"""Tests for Web Push: subscription storage, the send path, and pruning.

The push service itself is never contacted. ``webpush`` is replaced with a spy
so the tests can assert exactly what the server would send — the payload shape,
who it goes to, and that a permanently-gone endpoint is pruned — which is the
part of the feature that lives in this repository. Signing and transport are
``pywebpush``'s job and are covered by its own suite.
"""

import base64
import json

import pytest
from sqlalchemy import select

from app import push as push_module
from app.config import settings
from app.models import PushSubscription


def b64key(length: int = 65, seed: int = 0) -> str:
    """A syntactically valid subscription key (base64url of `length` bytes)."""
    return (
        base64.urlsafe_b64encode(bytes((i + seed) % 256 for i in range(length))).decode().rstrip("=")
    )


def vapid_public(seed: int = 1) -> str:
    """A VAPID public key shaped the way a browser requires it."""
    raw = bytes([0x04]) + bytes((i + seed) % 256 for i in range(64))
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def endpoint(name: str = "a") -> str:
    return f"https://push.example.com/{name}"


@pytest.fixture
def vapid(monkeypatch):
    """Pretend VAPID is configured, since the real keys are deployment secrets."""
    monkeypatch.setattr(settings, "vapid_public_key", vapid_public())
    monkeypatch.setattr(settings, "vapid_private_key", b64key(32, seed=99))
    monkeypatch.setattr(settings, "vapid_subject", "mailto:test@example.com")


@pytest.fixture
def sent(monkeypatch):
    """Capture every `webpush` call instead of performing one.

    Returns the list of calls, each a dict of the kwargs `pywebpush` received.
    """
    calls: list[dict] = []

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: calls.append(kwargs))
    return calls


def subscribe(client, headers, name: str = "a", **overrides):
    body = {
        "endpoint": endpoint(name),
        "keys": {"p256dh": b64key(), "auth": b64key(16)},
        **overrides,
    }
    return client.post("/api/push/subscribe", headers=headers, json=body)


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def test_status_reports_disabled_without_keys(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "vapid_public_key", None)
    monkeypatch.setattr(settings, "vapid_private_key", None)
    headers, _ = make_user("ada")

    body = client.get("/api/push/status", headers=headers).json()
    assert body == {"enabled": False, "public_key": None, "devices": 0}


def test_status_reports_disabled_for_a_wrongly_shaped_public_key(client, make_user, monkeypatch):
    """A 64-byte `x||y` key must be refused, not served to the browser.

    A browser rejects that form with `InvalidAccessError` inside `subscribe()`,
    which the user sees as a toggle that flips on and silently fails. Reporting
    push as unavailable keeps the problem visible at configuration time.
    """
    monkeypatch.setattr(settings, "vapid_public_key", b64key(64))
    monkeypatch.setattr(settings, "vapid_private_key", b64key(32))
    headers, _ = make_user("ada")

    body = client.get("/api/push/status", headers=headers).json()
    assert body["enabled"] is False
    assert body["public_key"] is None


def test_status_reports_disabled_for_a_key_without_the_uncompressed_prefix(client, make_user, monkeypatch):
    """65 bytes is not enough; the first byte has to be the 0x04 point marker."""
    wrong = base64.urlsafe_b64encode(bytes([0x02]) + bytes(64)).decode().rstrip("=")
    monkeypatch.setattr(settings, "vapid_public_key", wrong)
    monkeypatch.setattr(settings, "vapid_private_key", b64key(32))
    headers, _ = make_user("ada")

    assert client.get("/api/push/status", headers=headers).json()["enabled"] is False


def test_status_exposes_the_public_key_only(client, make_user, vapid):
    headers, _ = make_user("ada")

    body = client.get("/api/push/status", headers=headers).json()
    assert body["enabled"] is True
    assert body["public_key"] == settings.valid_vapid_public_key
    # The private key must never leave the server.
    assert settings.vapid_private_key not in json.dumps(body)


def test_push_endpoints_require_authentication(client):
    assert client.get("/api/push/status").status_code == 401
    assert client.post("/api/push/subscribe", json={}).status_code == 401
    assert client.post("/api/push/unsubscribe", json={"endpoint": endpoint()}).status_code == 401


# --------------------------------------------------------------------------
# Subscribing
# --------------------------------------------------------------------------


def test_subscribe_records_a_device(client, make_user, vapid):
    headers, _ = make_user("ada")

    response = subscribe(client, headers)
    assert response.status_code == 201, response.text
    assert response.json() == {"devices": 1}

    status = client.get("/api/push/status", headers=headers).json()
    assert status["devices"] == 1


def test_subscribe_is_idempotent_for_the_same_endpoint(client, make_user, vapid):
    """A browser re-subscribing after a key rotation must not create a second row."""
    headers, _ = make_user("ada")

    subscribe(client, headers)
    again = subscribe(client, headers)
    assert again.status_code == 201
    assert again.json() == {"devices": 1}


def test_resubscribe_moves_the_endpoint_to_the_new_user(client, make_user, vapid):
    """A shared browser signed into another account must not keep pushing to the old one."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")

    subscribe(client, ada)
    assert client.get("/api/push/status", headers=ada).json()["devices"] == 1

    subscribe(client, grace)
    assert client.get("/api/push/status", headers=grace).json()["devices"] == 1
    # The endpoint now belongs to grace alone.
    assert client.get("/api/push/status", headers=ada).json()["devices"] == 0


def test_subscribe_rejects_non_https_endpoint(client, make_user, vapid):
    headers, _ = make_user("ada")

    response = subscribe(client, headers, endpoint="http://insecure.example.com/x")
    assert response.status_code == 400


def test_subscribe_rejects_malformed_keys(client, make_user, vapid):
    headers, _ = make_user("ada")

    response = subscribe(client, headers, keys={"p256dh": "not base64!!", "auth": b64key(16)})
    assert response.status_code == 400


def test_subscribe_is_unavailable_without_keys(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "vapid_public_key", None)
    monkeypatch.setattr(settings, "vapid_private_key", None)
    headers, _ = make_user("ada")

    assert subscribe(client, headers).status_code == 503


# --------------------------------------------------------------------------
# Unsubscribing
# --------------------------------------------------------------------------


def test_unsubscribe_removes_only_the_callers_device(client, make_user, vapid):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    subscribe(client, ada, "shared")
    subscribe(client, grace, "shared")

    # Grace tries to drop the endpoint; it is hers now, so it goes.
    dropped = client.post(
        "/api/push/unsubscribe", headers=grace, json={"endpoint": endpoint("shared")}
    )
    assert dropped.status_code == 200
    assert dropped.json() == {"devices": 0}
    assert client.get("/api/push/status", headers=ada).json()["devices"] == 0


def test_unsubscribe_of_an_unknown_endpoint_is_a_no_op(client, make_user, vapid):
    headers, _ = make_user("ada")
    subscribe(client, headers)

    response = client.post(
        "/api/push/unsubscribe", headers=headers, json={"endpoint": endpoint("never-seen")}
    )
    assert response.status_code == 200
    assert response.json() == {"devices": 1}


def test_unsubscribe_cannot_drop_another_users_endpoint(client, make_user, vapid):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    subscribe(client, ada, "ada-phone")
    subscribe(client, grace, "grace-phone")

    client.post("/api/push/unsubscribe", headers=grace, json={"endpoint": endpoint("ada-phone")})

    assert client.get("/api/push/status", headers=ada).json()["devices"] == 1


# --------------------------------------------------------------------------
# The send path
# --------------------------------------------------------------------------


def test_a_direct_message_pushes_to_the_recipient(client, make_user, vapid, monkeypatch):
    """The core promise: a message reaches a recipient who has no tab open."""
    calls: list[tuple] = []

    def fake_notify_user(db, user_id, payload):
        calls.append((user_id, payload))
        return 1

    monkeypatch.setattr("app.routers.messages.push.notify_user", fake_notify_user)

    ada, ada_user = make_user("ada")
    grace, grace_user = make_user("grace")
    conversation = client.post(
        "/api/conversations", headers=ada, json={"username": "grace"}
    ).json()
    cid = conversation["id"]

    sent = client.post(
        f"/api/conversations/{cid}/messages", headers=ada, json={"body": "dinner at 8?"}
    )
    assert sent.status_code == 201, sent.text

    assert len(calls) == 1
    recipient, payload = calls[0]
    assert recipient == grace_user["id"]
    assert payload["title"] == "Ada"
    assert payload["body"] == "dinner at 8?"
    assert payload["url"] == f"/messages/{cid}"
    assert payload["type"] == "message"


def test_the_sender_is_not_pushed_their_own_message(client, make_user, vapid, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        "app.routers.messages.push.notify_user",
        lambda db, user_id, payload: calls.append(user_id) or 1,
    )

    ada, _ = make_user("ada")
    make_user("grace")
    cid = client.post("/api/conversations", headers=ada, json={"username": "grace"}).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", headers=ada, json={"body": "hi"})

    # Exactly the other participant, never the author.
    assert len(calls) == 1


def test_a_like_pushes_to_the_post_author(client, make_user, vapid, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        push_module,
        "schedule_notify",
        lambda user_id, payload: calls.append((user_id, payload)),
    )

    author, author_user = make_user("author")
    fan, _ = make_user("fan")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello"}).json()["id"]

    liked = client.post(f"/api/posts/{post_id}/like", headers=fan)
    assert liked.status_code in (200, 201), liked.text

    recipients = [user_id for user_id, _ in calls]
    assert author_user["id"] in recipients
    payload = next(p for uid, p in calls if uid == author_user["id"])
    assert payload["title"] == "Fan"
    assert payload["body"] == "Fan liked your post"
    assert payload["url"] == f"/p/{post_id}"


def test_a_follow_pushes_to_the_followed_user(client, make_user, vapid, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        push_module,
        "schedule_notify",
        lambda user_id, payload: calls.append((user_id, payload)),
    )

    ada, _ = make_user("ada")
    grace, grace_user = make_user("grace")

    followed = client.post("/api/users/grace/follow", headers=ada)
    assert followed.status_code in (200, 201), followed.text

    recipients = [user_id for user_id, _ in calls]
    assert grace_user["id"] in recipients
    payload = next(p for uid, p in calls if uid == grace_user["id"])
    assert payload["body"] == "Ada started following you"


def test_no_push_is_attempted_without_keys(client, make_user, monkeypatch):
    """A deployment with no VAPID keys must not try (or fail) to push."""
    monkeypatch.setattr(settings, "vapid_public_key", None)
    monkeypatch.setattr(settings, "vapid_private_key", None)
    attempted: list = []
    monkeypatch.setattr(push_module, "send_to_subscription", lambda *a, **k: attempted.append(a) or True)

    author, author_user = make_user("author")
    fan, _ = make_user("fan")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello"}).json()["id"]
    liked = client.post(f"/api/posts/{post_id}/like", headers=fan)
    assert liked.status_code in (200, 201), liked.text

    # The notification is still created and pushed over the socket; only the
    # Web Push transport is skipped, and no signing is attempted.
    assert attempted == []
    listed = client.get("/api/notifications", headers=author).json()
    assert any(n["kind"] == "like" for n in listed)


# --------------------------------------------------------------------------
# Delivery mechanics
# --------------------------------------------------------------------------


def test_schedule_notify_does_not_block_the_caller(vapid, monkeypatch):
    """The dispatch must return immediately, not wait for the push service.

    `pywebpush` is synchronous, so sending inline in an `async` handler stalls
    the event loop for the length of a network round trip. This is the guard:
    the call returns while the (slow) send is still running.
    """
    import threading
    import time

    started = threading.Event()
    release = threading.Event()

    def slow_send(db, user_id, payload):
        started.set()
        release.wait(timeout=5)
        return 1

    monkeypatch.setattr(push_module, "notify_user", slow_send)

    t0 = time.perf_counter()
    push_module.schedule_notify(1, {"body": "hi"})
    elapsed = time.perf_counter() - t0
    try:
        assert elapsed < 0.5, f"schedule_notify blocked for {elapsed:.3f}s"
        assert started.wait(timeout=5), "the send never started on a worker thread"
    finally:
        release.set()


def test_schedule_notify_uses_its_own_session(vapid, monkeypatch):
    """The worker must open its own session; the request's is already closed."""
    import threading

    sessions: list = []
    done = threading.Event()

    class FakeSession:
        def __init__(self):
            sessions.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr("app.database.SessionLocal", FakeSession)

    def record(db, user_id, payload):
        sessions[-1].used = db
        done.set()
        return 1

    monkeypatch.setattr(push_module, "notify_user", record)

    push_module.schedule_notify(7, {"body": "hi"})
    assert done.wait(timeout=5)
    assert len(sessions) == 1
    assert sessions[0].used is sessions[0]
    assert getattr(sessions[0], "closed", False) is True


def test_schedule_notify_is_a_noop_without_keys(monkeypatch):
    """Nothing should be dispatched (or spawned) when VAPID is unconfigured."""
    monkeypatch.setattr(settings, "vapid_public_key", None)
    monkeypatch.setattr(settings, "vapid_private_key", None)

    import threading

    monkeypatch.setattr(
        threading, "Thread", lambda *a, **k: pytest.fail("a thread was spawned")
    )
    push_module.schedule_notify(1, {"body": "hi"})


def test_notify_user_sends_to_every_device(client, make_user, vapid, monkeypatch):
    headers, user = make_user("ada")
    subscribe(client, headers, "phone")
    subscribe(client, headers, "laptop")

    delivered: list[str] = []

    def fake_send(subscription, payload):
        delivered.append(subscription.endpoint)
        return True

    monkeypatch.setattr(push_module, "send_to_subscription", fake_send)

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        count = push_module.notify_user(session, user["id"], {"title": "x", "body": "y"})
    finally:
        session.close()

    assert count == 2
    assert sorted(delivered) == [endpoint("laptop"), endpoint("phone")]


def test_notify_user_prunes_an_endpoint_the_service_reports_gone(
    client, make_user, vapid, monkeypatch
):
    """A 410 from the push service must delete the row, not be retried forever."""
    headers, user = make_user("ada")
    subscribe(client, headers)

    def gone(subscription, payload):
        raise type(
            "WebPushException", (Exception,), {"response": type("R", (), {"status_code": 410})()}
        )()

    monkeypatch.setattr(push_module, "send_to_subscription", gone)

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        count = push_module.notify_user(session, user["id"], {"title": "x"})
        remaining = session.scalars(select(PushSubscription)).all()
    finally:
        session.close()

    assert count == 0
    assert remaining == []


def test_a_failing_push_does_not_break_the_request(client, make_user, vapid, monkeypatch):
    """The message is already committed; a dead push service must not fail the send."""
    headers, user = make_user("ada")
    subscribe(client, headers)

    def boom(subscription, payload):
        raise RuntimeError("push service unreachable")

    monkeypatch.setattr(push_module, "send_to_subscription", boom)

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        assert push_module.notify_user(session, user["id"], {"title": "x"}) == 0
    finally:
        session.close()


def test_send_to_subscription_builds_a_signed_request(client, make_user, vapid, monkeypatch):
    """The payload is JSON and the VAPID claims carry the configured subject."""
    calls: list[dict] = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)

    headers, user = make_user("ada")
    subscribe(client, headers)

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        subscription = session.scalars(select(PushSubscription)).one()
        assert push_module.send_to_subscription(subscription, {"title": "hi", "body": "there"})
    finally:
        session.close()

    assert len(calls) == 1
    call = calls[0]
    assert json.loads(call["data"]) == {"title": "hi", "body": "there"}
    assert call["vapid_claims"]["sub"] == "mailto:test@example.com"
    assert call["subscription_info"]["endpoint"] == endpoint()
    assert call["subscription_info"]["keys"]["p256dh"] == b64key()


def test_send_is_skipped_when_not_configured(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "vapid_public_key", None)
    monkeypatch.setattr(settings, "vapid_private_key", None)

    from app.models import PushSubscription

    subscription = PushSubscription(endpoint=endpoint(), p256dh=b64key(), auth=b64key(16))
    assert push_module.send_to_subscription(subscription, {"title": "x"}) is False


# --------------------------------------------------------------------------
# Payload helpers
# --------------------------------------------------------------------------


def test_notification_payload_names_the_actor():
    event = {
        "notification": {"id": 7, "kind": "like", "post_id": 3},
        "actor": {"display_name": "Ada Lovelace"},
    }
    payload = push_module.notification_payload(event, "Ada Lovelace liked your post", "/p/3")

    assert payload["title"] == "Ada Lovelace"
    assert payload["body"] == "Ada Lovelace liked your post"
    assert payload["url"] == "/p/3"
    # Tagged by notification id so a repeat collapses into one alert.
    assert payload["tag"] == "notification-7"


def test_generated_keys_are_usable_by_a_browser():
    """A generated pair must be shaped the way VAPID and browsers require.

    The public key regression: it was emitted as a bare 64-byte `x||y` point,
    which `PushManager.subscribe` rejects with `InvalidAccessError`. The
    uncompressed `0x04 || x || y` form is the only one a browser accepts.
    """
    private, public = push_module.generate_keys()

    private_raw = base64.urlsafe_b64decode(private + "=" * (-len(private) % 4))
    public_raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    assert len(private_raw) == 32
    assert len(public_raw) == 65
    assert public_raw[0] == 0x04

    # And it is a real key on the curve, not merely the right length.
    from py_vapid import Vapid02

    vapid = Vapid02.from_raw(private.encode())
    assert vapid.public_key is not None

    # The generated public key is served to browsers unchanged.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "vapid_public_key", public)
        patch.setattr(settings, "vapid_private_key", private)
        assert settings.push_configured is True
        assert settings.valid_vapid_public_key == public


def test_the_public_key_is_the_one_that_matches_the_private_key():
    """A browser subscribes against the public key; sends are signed with the
    private one. If the two are not a pair the push service rejects every send
    with 403, so the pair has to be checked as a pair, not just for shape.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private, public = push_module.generate_keys()
    private_raw = base64.urlsafe_b64decode(private + "=" * (-len(private) % 4))
    public_raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))

    derived = (
        ec.derive_private_key(int.from_bytes(private_raw, "big"), ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    assert derived == public_raw


def test_generate_keys_does_not_reuse_a_pair():
    """Every call must mint a new key, or a redeploy would silently keep the
    old pair and every stored subscription would be invalidated for nothing."""
    first = push_module.generate_keys()
    second = push_module.generate_keys()
    assert first != second


def test_the_documented_command_prints_a_key_pair():
    """`python -m app.push` is what `render.yaml` and the README tell an operator
    to run. It printed nothing from the wrong module once, which fails silently:
    the command exits 0 and the operator pastes an empty value into Render.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "app.push"],
        capture_output=True,
        text=True,
        cwd="/workspace/project/backend",
    )
    assert result.returncode == 0, result.stderr
    assert "VAPID_PRIVATE_KEY=" in result.stdout
    assert "VAPID_PUBLIC_KEY=" in result.stdout

    private = next(
        line.split("=", 1)[1] for line in result.stdout.splitlines()
        if line.startswith("VAPID_PRIVATE_KEY=")
    )
    public = next(
        line.split("=", 1)[1] for line in result.stdout.splitlines()
        if line.startswith("VAPID_PUBLIC_KEY=")
    )
    # Both halves must be non-empty and the public key must be browser-shaped.
    assert private
    public_raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    assert len(public_raw) == 65 and public_raw[0] == 0x04
