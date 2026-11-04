"""Tests for Web Push: subscription storage, the send path, and pruning.

The push service itself is never contacted. ``webpush`` is replaced with a spy
so the tests can assert exactly what the server would send — the payload shape,
who it goes to, and that a permanently-gone endpoint is pruned — which is the
part of the feature that lives in this repository. Signing and transport are
``pywebpush``'s job and are covered by its own suite.
"""

import base64
import json
import logging

import pytest
from sqlalchemy import select

from app import push as push_module
from app.config import Settings, settings
from app.models import PushSubscription

from .test_media import PNG


def b64key(length: int = 65, seed: int = 0) -> str:
    """A syntactically valid subscription key (base64url of `length` bytes)."""
    return (
        base64.urlsafe_b64encode(bytes((i + seed) % 256 for i in range(length))).decode().rstrip("=")
    )


def wait_for_calls(calls: list, count: int = 1, timeout: float = 5.0) -> None:
    """Block until a background delivery has been recorded.

    ``schedule_notify`` hands the send to a worker thread, so a request
    returning does not mean the spy has been called yet. Asserting straight
    after the response is a race that a loaded machine loses, so the tests wait
    on the observable effect rather than on a fixed sleep.
    """
    import time

    deadline = time.monotonic() + timeout
    while len(calls) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) >= count, f"expected {count} push call(s), saw {len(calls)}"


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
    assert body == {
        "enabled": False,
        "public_key": None,
        "devices": 0,
        "endpoint_registered": None,
    }


def test_status_reports_whether_the_callers_endpoint_is_registered(client, make_user, vapid):
    """The caller's own endpoint is the one thing only the caller can check.

    A device whose row was pruned still holds its browser subscription, so the
    client has to be told the server no longer has it before it can re-subscribe.
    """
    headers, _ = make_user("ada")
    subscribe(client, headers, "a")

    # No endpoint given: nothing to report about.
    assert client.get("/api/push/status", headers=headers).json()["endpoint_registered"] is None

    # The endpoint this user registered is on file.
    registered = client.get(
        "/api/push/status", headers=headers, params={"endpoint": endpoint("a")}
    ).json()
    assert registered["endpoint_registered"] is True

    # One this user never registered is not.
    missing = client.get(
        "/api/push/status", headers=headers, params={"endpoint": endpoint("z")}
    ).json()
    assert missing["endpoint_registered"] is False


def test_status_does_not_report_another_users_endpoint_as_the_callers(client, make_user, vapid):
    """The lookup is scoped to the caller, so a shared browser cannot mislead."""
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    subscribe(client, ada_headers, "a")

    body = client.get(
        "/api/push/status", headers=grace_headers, params={"endpoint": endpoint("a")}
    ).json()
    assert body["endpoint_registered"] is False


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


def test_resubscribe_links_the_endpoint_to_the_second_account_too(client, make_user, vapid):
    """A shared browser signed into two accounts must alert for both.

    This is the bug the link table fixes: the endpoint used to be reassigned to
    whoever signed in last, so the account you were *not* using went silent —
    exactly the alert Web Push exists to deliver.
    """
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")

    subscribe(client, ada)
    assert client.get("/api/push/status", headers=ada).json()["devices"] == 1

    subscribe(client, grace)
    # Both accounts now hold the endpoint.
    assert client.get("/api/push/status", headers=grace).json()["devices"] == 1
    assert client.get("/api/push/status", headers=ada).json()["devices"] == 1

    # And each still reports the shared endpoint as registered to it.
    assert client.get(
        "/api/push/status", headers=ada, params={"endpoint": endpoint("a")}
    ).json()["endpoint_registered"] is True
    assert client.get(
        "/api/push/status", headers=grace, params={"endpoint": endpoint("a")}
    ).json()["endpoint_registered"] is True


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


def test_unsubscribe_removes_only_the_callers_link(client, make_user, vapid):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    subscribe(client, ada, "shared")
    subscribe(client, grace, "shared")

    # Grace drops her link; Ada's is untouched, so the shared browser still
    # alerts for the account that is not in front.
    dropped = client.post(
        "/api/push/unsubscribe", headers=grace, json={"endpoint": endpoint("shared")}
    )
    assert dropped.status_code == 200
    assert dropped.json() == {"devices": 0}
    assert client.get("/api/push/status", headers=ada).json()["devices"] == 1
    assert client.get(
        "/api/push/status", headers=ada, params={"endpoint": endpoint("shared")}
    ).json()["endpoint_registered"] is True


def test_unsubscribe_all_releases_the_whole_endpoint(client, make_user, vapid):
    """Switching the push switch off means "nothing from this browser"."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    subscribe(client, ada, "shared")
    subscribe(client, grace, "shared")

    dropped = client.post(
        "/api/push/unsubscribe",
        headers=grace,
        json={"endpoint": endpoint("shared"), "all": True},
    )
    assert dropped.status_code == 200
    assert client.get("/api/push/status", headers=ada).json()["devices"] == 0
    assert client.get("/api/push/status", headers=grace).json()["devices"] == 0


def test_unsubscribe_all_cannot_release_another_accounts_endpoint(client, make_user, vapid):
    """`all` is still scoped through the caller's own link."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    subscribe(client, ada, "ada-phone")
    subscribe(client, grace, "grace-phone")

    client.post(
        "/api/push/unsubscribe",
        headers=grace,
        json={"endpoint": endpoint("ada-phone"), "all": True},
    )

    assert client.get("/api/push/status", headers=ada).json()["devices"] == 1


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

    wait_for_calls(calls)
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
    wait_for_calls(calls)
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


def test_schedule_notify_does_not_spawn_a_thread_per_notification(vapid, monkeypatch):
    """A burst must not create one thread per notification.

    Sending on a thread per event is unbounded: a slow push service makes every
    send outlive its request, so a burst piles up threads — each holding a DB
    session — until the process hits its memory ceiling. The pool is fixed, so
    the thread count must stay flat while the sends are in flight.
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
    push_module._dispatcher.reset()

    before = threading.active_count()
    try:
        for i in range(200):
            push_module.schedule_notify(i, {"body": "hi"})
        assert started.wait(timeout=5), "no send ever started"
        # Give any (buggy) extra threads a moment to appear before counting.
        time.sleep(0.2)
        after = threading.active_count()
    finally:
        release.set()

    spawned = after - before
    assert spawned <= push_module.PUSH_WORKERS, (
        f"{spawned} threads spawned for 200 notifications; "
        f"the pool should be capped at {push_module.PUSH_WORKERS}"
    )


def test_schedule_notify_drops_when_the_backlog_is_full(vapid, monkeypatch):
    """A full queue drops the newest event instead of growing without bound."""
    import threading

    release = threading.Event()
    started = threading.Event()

    def slow_send(db, user_id, payload):
        started.set()
        release.wait(timeout=5)
        return 1

    monkeypatch.setattr(push_module, "notify_user", slow_send)
    push_module._dispatcher.reset()

    try:
        accepted = sum(
            push_module._dispatcher.submit(i, {"body": "hi"})
            for i in range(push_module.PUSH_QUEUE_MAX + push_module.PUSH_WORKERS + 25)
        )
        assert push_module._dispatcher.dropped > 0, "the queue grew past its ceiling"
        # Everything accepted fits in the queue plus what the workers took.
        assert accepted <= push_module.PUSH_QUEUE_MAX + push_module.PUSH_WORKERS
    finally:
        release.set()
        push_module._dispatcher.reset()


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


def test_notify_user_reaches_an_account_sharing_a_browser(client, make_user, vapid, monkeypatch):
    """The core multi-account guarantee.

    Two accounts on one browser share a single push endpoint. A notification for
    the account that is *not* in front must still be delivered — that is the
    whole reason Web Push exists — and the payload must say which account it is
    for, since the screen gives no clue.
    """
    ada, ada_user = make_user("ada")
    grace, grace_user = make_user("grace")
    subscribe(client, ada, "shared")
    subscribe(client, grace, "shared")

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        push_module,
        "send_to_subscription",
        lambda subscription, payload: calls.append((subscription.endpoint, payload)) or True,
    )

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        # Grace is the account in the background; Ada is signed in "in front".
        count = push_module.notify_user(session, grace_user["id"], {"title": "Ada", "body": "hi"})
    finally:
        session.close()

    assert count == 1
    assert calls[0][0] == endpoint("shared")
    # The label tells the user which account the alert belongs to.
    assert calls[0][1]["account"] == "Grace"
    assert calls[0][1]["account_id"] == grace_user["id"]


def test_notify_user_leaves_a_single_account_payload_unlabelled(client, make_user, vapid, monkeypatch):
    """One account on a browser needs no label: the alert is obviously theirs."""
    headers, user = make_user("ada")
    subscribe(client, headers, "solo")

    payloads: list[dict] = []
    monkeypatch.setattr(
        push_module,
        "send_to_subscription",
        lambda subscription, payload: payloads.append(payload) or True,
    )

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        push_module.notify_user(session, user["id"], {"title": "x", "body": "y"})
    finally:
        session.close()

    assert "account" not in payloads[0]


def test_a_pruned_endpoint_stops_being_retried_for_every_linked_account(
    client, make_user, vapid, monkeypatch
):
    """A dead endpoint is deleted once, taking its links with it."""
    ada, ada_user = make_user("ada")
    grace, grace_user = make_user("grace")
    subscribe(client, ada, "shared")
    subscribe(client, grace, "shared")

    def gone(subscription, payload):
        raise RuntimeError("410 Gone")

    monkeypatch.setattr(push_module, "send_to_subscription", gone)

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        push_module.notify_user(session, ada_user["id"], {"title": "x"})
        push_module.notify_user(session, grace_user["id"], {"title": "x"})
    finally:
        session.close()

    assert client.get("/api/push/status", headers=ada).json()["devices"] == 0
    assert client.get("/api/push/status", headers=grace).json()["devices"] == 0


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


def _webpush_error(status: int, body: str):
    """A `WebPushException` shaped like the one `pywebpush` raises."""
    from pywebpush import WebPushException

    response = type("Response", (), {"status_code": status, "text": body, "content": body.encode()})()
    return WebPushException("boom", response=response)


def test_a_vapid_key_mismatch_is_recognised_as_permanent():
    """The phrase FCM returns after a key rotation is the signal, not the 403."""
    mismatch = (
        "the VAPID credentials in the authorization header do not correspond to "
        "the credentials used to create the subscriptions."
    )
    assert push_module._is_vapid_mismatch(
        type("R", (), {"status_code": 403, "text": mismatch, "content": mismatch.encode()})()
    )
    # A 403 for some other reason must stay retryable.
    assert not push_module._is_vapid_mismatch(
        type("R", (), {"status_code": 403, "text": "forbidden", "content": b"forbidden"})()
    )
    assert not push_module._is_vapid_mismatch(None)


def test_notify_user_prunes_a_subscription_the_service_rejects_for_a_key_change(
    client, make_user, vapid, monkeypatch
):
    """After a VAPID rotation the row can never deliver, so it must be removed.

    FCM refuses with 403 rather than 404/410, so without this the row survives
    and every future alert is silently discarded while the toggle still reads
    "subscribed".
    """
    headers, user = make_user("ada")
    subscribe(client, headers)

    mismatch = (
        "the VAPID credentials in the authorization header do not correspond to "
        "the credentials used to create the subscriptions."
    )

    def rejected(**kwargs):
        raise _webpush_error(403, mismatch)

    # Patch the transport, not `send_to_subscription`: the decision to treat a
    # 403 as permanent is made inside `send_to_subscription`, so replacing it
    # would test the stub instead of the rule.
    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", rejected)


    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        assert push_module.notify_user(session, user["id"], {"title": "x"}) == 0
        assert session.scalars(select(PushSubscription)).all() == []
    finally:
        session.close()


def test_a_transient_403_keeps_the_subscription(client, make_user, vapid, monkeypatch):
    """A 403 that is not a key mismatch may be temporary, so the row is kept."""
    headers, user = make_user("ada")
    subscribe(client, headers)

    def rejected(**kwargs):
        raise _webpush_error(403, "forbidden")

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", rejected)

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        assert push_module.notify_user(session, user["id"], {"title": "x"}) == 0
        assert len(session.scalars(select(PushSubscription)).all()) == 1
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
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, "-m", "app.push"],
        capture_output=True,
        text=True,
        # The module lives at the backend root, one level above this test file.
        cwd=Path(__file__).resolve().parents[1],
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

# --- Apple's VAPID subject rule ---------------------------------------------
#
# Apple refuses the whole request with 403 BadJwtToken when the `sub` claim is
# not a deliverable URL or mailto address. FCM accepts the same request, so the
# only symptom is "Android is fine, iPhone gets nothing" -- which is exactly the
# bug this guards. Verified against web.push.apple.com: `.local` returns 403
# BadJwtToken while a real domain is accepted and fails only on the device token.


@pytest.mark.parametrize(
    "subject",
    [
        "mailto:no-reply@pulse.local",  # the placeholder that caused the bug
        "mailto:admin@localhost",
        "mailto:a@b.invalid",
        "mailto:a@b.test",
        "mailto:a@b.internal",
        "mailto:a@b",  # no dot, so no real TLD
        "mailto:a@b.LOCAL",  # case must not matter
        "no-reply@pulse.app",  # not a mailto: or https: URI at all
        "mailto: <a@b.com>",  # display-name form Apple also rejects
        "mailto:a b@c.com",  # whitespace in the address
        "",
    ],
)
def test_a_subject_apple_would_reject_is_flagged(subject, monkeypatch):
    monkeypatch.setattr(settings, "vapid_subject", subject)
    assert settings.push_subject_valid is False


@pytest.mark.parametrize(
    "subject",
    [
        "mailto:someone@yourdomain.com",
        # A public TLD is the bar, not a mailbox that exists: Apple does not
        # do MX lookups, so a documentation domain on a real TLD passes.
        "mailto:someone@example.com",
        "https://pulse-hazel-nine.vercel.app",
        "https://example.org/contact",
    ],
)
def test_a_deliverable_subject_is_accepted(subject, monkeypatch):
    monkeypatch.setattr(settings, "vapid_subject", subject)
    assert settings.push_subject_valid is True


def test_the_shipped_default_subject_is_deliverable():
    """The default must not be the thing that breaks iPhone push.

    This is the regression guard for the actual bug: the default was
    `mailto:no-reply@pulse.local`, which every local check passed and Apple
    refused, so the failure only ever appeared on a real iPhone.
    """
    assert Settings().push_subject_valid is True


def test_the_boot_warning_reaches_the_log_not_stdout(monkeypatch, caplog):
    """A misconfiguration must be reported through `logging`, not `print`.

    stdout is block-buffered when it is not a TTY, which is exactly how a
    process manager runs the server, so a printed warning can stay in the
    buffer and never appear in the deployment's logs -- defeating the point of
    warning at boot at all.
    """
    from app import main as main_module

    monkeypatch.setattr(settings, "vapid_subject", "mailto:no-reply@pulse.local")
    monkeypatch.setattr(settings, "vapid_public_key", vapid_public())
    monkeypatch.setattr(settings, "vapid_private_key", b64key(32))

    with caplog.at_level(logging.WARNING, logger="pulse.main"):
        main_module._warn_on_push_misconfiguration()

    assert any("BadJwtToken" in r.getMessage() for r in caplog.records)


def test_a_valid_subject_does_not_warn(monkeypatch, caplog):
    from app import main as main_module

    monkeypatch.setattr(settings, "vapid_subject", "mailto:someone@yourdomain.com")
    monkeypatch.setattr(settings, "vapid_public_key", vapid_public())
    monkeypatch.setattr(settings, "vapid_private_key", b64key(32))

    with caplog.at_level(logging.WARNING, logger="pulse.main"):
        main_module._warn_on_push_misconfiguration()

    assert not caplog.records


def test_a_bad_subject_is_reported_as_configuration_not_a_dead_device(
    client, make_user, vapid, monkeypatch, caplog
):
    """A BadJwtToken refusal must not prune the subscription.

    The endpoint is healthy and would deliver once the subject is corrected, so
    deleting the row would make every iPhone user re-enable notifications for a
    problem they did not cause and cannot see.
    """
    headers, user = make_user("ada")
    subscribe(client, headers)

    def rejected(**kwargs):
        raise _webpush_error(403, '{"reason":"BadJwtToken"}')

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", rejected)

    from app.database import get_db

    session = next(client.app.dependency_overrides[get_db]())
    try:
        with caplog.at_level("WARNING"):
            assert push_module.notify_user(session, user["id"], {"title": "x"}) == 0
        # The row survives, so a fixed deployment delivers without user action.
        assert len(session.scalars(select(PushSubscription)).all()) == 1
        assert "VAPID_SUBJECT" in caplog.text
    finally:
        session.close()


def test_a_bad_jwt_is_distinguished_from_a_key_mismatch():
    """Both are 403, but one prunes the row and the other must not."""
    bad_jwt = type(
        "R", (), {"status_code": 403, "text": '{"reason":"BadJwtToken"}', "content": b""}
    )()
    assert push_module._is_bad_jwt(bad_jwt)
    # The key-mismatch phrase is a different failure with the opposite remedy.
    mismatch = (
        "the VAPID credentials in the authorization header do not correspond to "
        "the credentials used to create the subscriptions."
    )
    mismatch_response = type("R", (), {"status_code": 403, "text": mismatch, "content": b""})()
    assert push_module._is_bad_jwt(mismatch_response) is False
    assert push_module._is_vapid_mismatch(mismatch_response) is True
    assert push_module._is_bad_jwt(None) is False


def test_the_blueprint_pins_a_subject_the_app_would_accept():
    """The Render blueprint must not leave VAPID_SUBJECT to the dashboard.

    A dashboard value wins over the application default, so declaring the key
    with `sync: false` let the broken `mailto:no-reply@pulse.local` outlive the
    deploy that corrected the default and keep iPhone push dead. The blueprint
    has to state the value, and it has to be one `push_subject_valid` accepts.
    """
    from pathlib import Path

    import yaml

    blueprint = Path(__file__).resolve().parents[2] / "render.yaml"
    doc = yaml.safe_load(blueprint.read_text())
    service = next(s for s in doc["services"] if s["name"] == "pulse-api")
    entries = {e["key"]: e for e in service["envVars"]}

    assert "VAPID_SUBJECT" in entries, "the blueprint must declare VAPID_SUBJECT"
    entry = entries["VAPID_SUBJECT"]
    assert "value" in entry, "the value must be pinned, not left to the dashboard"
    assert Settings(vapid_subject=entry["value"]).push_subject_valid


# --------------------------------------------------------------------------
# Branded notification icons
#
# An alert that shows only a logo loses who it is about; one that shows only a
# face loses where it came from. These cover the composed icon that carries
# both, the payload fields that point at it, and the service worker that reads
# them.
# --------------------------------------------------------------------------


def test_the_payload_names_the_branded_icon_for_the_actor(client, make_user, vapid, monkeypatch):
    """A like must point its icon at the liker's branded avatar, not the logo."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        push_module,
        "schedule_notify",
        lambda user_id, payload: calls.append((user_id, payload)),
    )

    author, author_user = make_user("author")
    fan, fan_user = make_user("fan")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello"}).json()["id"]
    client.post(f"/api/posts/{post_id}/like", headers=fan)

    payload = next(p for uid, p in calls if uid == author_user["id"])
    assert payload["icon"] == f"/api/push/icon/{fan_user['id']}.png"
    assert payload["badge"] == "/badge-96.png"


def test_a_direct_message_names_the_senders_icon(client, make_user, vapid, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.routers.messages.push.notify_user",
        lambda db, user_id, payload: calls.append((user_id, payload)) or 1,
    )

    ada, ada_user = make_user("ada")
    make_user("grace")
    cid = client.post("/api/conversations", headers=ada, json={"username": "grace"}).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", headers=ada, json={"body": "hi"})

    wait_for_calls(calls)
    _, payload = calls[0]
    assert payload["icon"] == f"/api/push/icon/{ada_user['id']}.png"


def test_the_icon_is_a_png_of_the_senders_avatar(client, make_user):
    """The route composes the sender's picture with the mark, and is public.

    Public because the OS fetches it with no session, which is the only way an
    alert for a closed browser can carry a picture at all.
    """
    import io

    from PIL import Image

    _, me = make_user("ada")
    # No avatar set: the initials tile is still a valid icon, so the route must
    # answer rather than 404 or serve nothing.
    response = client.get(f"/api/push/icon/{me['id']}.png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"].startswith("public")
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (192, 192)


def test_the_icon_uses_the_uploaded_picture_when_there_is_one(client, make_user):
    """A real avatar is composited, not replaced by initials."""
    import io

    from PIL import Image

    headers, me = make_user("ada")
    client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"file": ("me.png", io.BytesIO(PNG), "image/png")},
    )

    response = client.get(f"/api/push/icon/{me['id']}.png")
    assert response.status_code == 200
    image = Image.open(io.BytesIO(response.content)).convert("RGBA")
    assert image.size == (192, 192)
    # The middle is the avatar and the badge disc sits inset in the corner;
    # both must be opaque for the icon to be more than a bare avatar.
    assert image.getpixel((96, 96))[3] == 255
    assert image.getpixel((141, 141))[3] == 255


def test_the_icon_is_404_for_an_unknown_user(client):
    assert client.get("/api/push/icon/999999.png").status_code == 404


def test_the_icon_needs_no_authentication(client, make_user):
    """The OS draws the notification; it holds no bearer token."""
    _, me = make_user("ada")
    assert client.get(f"/api/push/icon/{me['id']}.png").status_code == 200


def test_the_service_worker_reads_the_icon_and_badge_from_the_payload():
    """The worker must forward the payload's icon, not a hard-coded logo.

    The payload is built by the API and read by the worker; a rename on one side
    silently reverts every alert to the generic logo, which no server test can
    catch. This reads the shipped worker source.
    """
    from pathlib import Path

    worker = Path(__file__).resolve().parents[2] / "frontend" / "public" / "sw.js"
    source = worker.read_text()
    assert "payload.icon" in source, "the worker must use the payload's icon"
    assert "payload.badge" in source, "the worker must use the payload's badge"
    # Relative paths must be resolved against the worker's origin, because the
    # OS has no page to resolve them against and drops an unfetchable icon.
    assert "self.location.origin" in source


def test_the_notification_badge_asset_is_monochrome():
    """Android tints the badge a single colour, so it must be a white silhouette."""
    import io
    from pathlib import Path

    from PIL import Image

    badge = Path(__file__).resolve().parents[2] / "frontend" / "public" / "badge-96.png"
    assert badge.exists(), "the badge asset must ship with the frontend"
    image = Image.open(io.BytesIO(badge.read_bytes())).convert("RGBA")
    assert image.size == (96, 96)
    opaque = [p for p in image.get_flattened_data() if p[3] > 0]
    assert opaque, "the badge must draw something"
    assert all(p[:3] == (255, 255, 255) for p in opaque), "the badge must be pure white"


