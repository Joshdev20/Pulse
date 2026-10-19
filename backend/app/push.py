"""Web Push delivery, so an alert reaches a user with no tab open.

An in-page popup and an OS notification both need a live page: the first needs
the tab visible, the second needs it merely open. Neither exists once the
browser is closed, which is the case this module covers. A stored subscription
is a push-service endpoint that the browser keeps alive at the OS level, so the
server can hand the push service a payload and have it wake the service worker
even with Pulse closed.

Delivery is best-effort and never raises into a request: a push is a side
channel, and a message that was already committed to the database must not fail
because a push service was slow or a subscription had gone stale.

``pywebpush`` is imported lazily. It pulls in ``cryptography`` and ``requests``,
and a deployment that never configured VAPID keys should not pay for the import
or fail to boot if the wheel is missing.
"""

import json
import logging
import threading
from collections import deque
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .models import PushSubscription, User, utcnow

logger = logging.getLogger("pulse.push")

# Push services answer 404/410 for an endpoint that is permanently gone (the
# browser unsubscribed, or the profile was removed). Those rows are deleted.
# Any other failure is treated as transient and the row is kept.
GONE_STATUSES = {404, 410}

# How long one delivery may take before it is abandoned, in seconds. This is
# the ceiling on how long a single push service can hold one of the pool's
# workers: while a worker is stuck on a slow endpoint, every other alert queued
# behind it waits. A shorter timeout bounds that head-of-line delay, and a push
# service that has not answered within a few seconds is not going to deliver
# fast enough to matter anyway.
PUSH_TIMEOUT_SECONDS = 5.0

# Delivery runs on a small fixed pool of long-lived threads, not one thread per
# notification. A thread per event is unbounded: a slow or hanging push service
# makes every send outlive its request, so a burst of notifications — or one
# account with many devices — piles up threads that each hold a DB session and a
# stack until the process hits its memory ceiling. A fixed pool caps that, and a
# bounded queue means the backlog is dropped rather than buffered without limit.
PUSH_WORKERS = 4
PUSH_QUEUE_MAX = 64


class _PushDispatcher:
    """A bounded worker pool that delivers pushes off the request path.

    ``schedule_notify`` must return immediately: it is called from ``async``
    handlers and ``pywebpush`` is synchronous, so an inline send would stall the
    event loop. That requirement is met by handing the work to one of a small,
    fixed set of daemon threads started on first use.

    Backpressure is deliberate. When the queue is full the newest event is
    dropped and counted, because push is a best-effort side channel: the
    notification itself is already committed and visible in the app, so losing
    an alert is strictly better than growing memory without bound.
    """

    def __init__(self, workers: int = PUSH_WORKERS, maxsize: int = PUSH_QUEUE_MAX) -> None:
        self._workers = workers
        self._maxsize = maxsize
        self._queue: deque[tuple[int, dict[str, Any]]] = deque()
        self._available = threading.Condition()
        self._started = False
        self._dropped = 0

    def submit(self, user_id: int, payload: dict[str, Any]) -> bool:
        """Queue one delivery. Returns False if the backlog is full."""
        with self._available:
            self._ensure_started()
            if len(self._queue) >= self._maxsize:
                self._dropped += 1
                if self._dropped % 100 == 1:
                    logger.warning(
                        "push backlog full (%d queued); dropped %d alert(s) so far",
                        self._maxsize,
                        self._dropped,
                    )
                return False
            self._queue.append((user_id, payload))
            self._available.notify()
            return True

    @property
    def dropped(self) -> int:
        return self._dropped

    def reset(self) -> None:
        """Forget the backlog and the drop count. Used by tests.

        The worker threads are left running and blocked on the condition; they
        are daemons and the next ``submit`` reuses them.
        """
        with self._available:
            self._queue.clear()
            self._dropped = 0

    def _ensure_started(self) -> None:
        """Start the pool once. Caller must hold ``_available``."""
        if self._started:
            return
        self._started = True
        for index in range(self._workers):
            threading.Thread(
                target=self._run,
                name=f"pulse-push-{index}",
                daemon=True,
            ).start()

    def _run(self) -> None:
        while True:
            with self._available:
                while not self._queue:
                    self._available.wait()
                user_id, payload = self._queue.popleft()
            # Outside the lock: a send is a network round trip and must not
            # block other workers from taking the next item off the queue.
            _deliver(user_id, payload)


_dispatcher = _PushDispatcher()


def schedule_notify(user_id: int, payload: dict[str, Any]) -> None:
    """Deliver ``payload`` to ``user_id`` without blocking the event loop.

    ``pywebpush`` uses synchronous ``requests``, so a send costs a few hundred
    milliseconds. Running it inline inside an ``async`` handler would stall the
    event loop and every other request with it, so the send is handed to the
    dispatcher's worker pool. The request returns immediately; the push is
    best-effort.
    """
    if not settings.push_configured:
        return
    _dispatcher.submit(user_id, payload)


def generate_keys() -> tuple[str, str]:
    """Create a VAPID key pair as base64url strings (private, public).

    The public key is the 65-byte uncompressed EC point (``0x04 || x || y``),
    which is what a browser's ``applicationServerKey`` requires. Handing it the
    64-byte ``x || y`` form instead is rejected with an ``InvalidAccessError``
    at subscribe time, so the prefix byte is not optional.
    """
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid02
    from py_vapid.utils import b64urlencode

    key = Vapid02()
    key.generate_keys()
    private_raw = key.private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return b64urlencode(private_raw), b64urlencode(public_raw)


def _subscription_info(subscription: PushSubscription) -> dict[str, Any]:
    return {
        "endpoint": subscription.endpoint,
        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
    }


def send_to_subscription(subscription: PushSubscription, payload: dict[str, Any]) -> bool:
    """Deliver one payload to one browser.

    Returns True when the push service accepted it, False otherwise. Raises
    nothing: every failure mode (no keys configured, an unreachable service, a
    malformed subscription) is reported through the return value so a caller can
    decide whether to prune the row.
    """
    if not settings.push_configured:
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover - only when the wheel is absent
        logger.warning("pywebpush is not installed; Web Push is disabled")
        return False

    try:
        webpush(
            subscription_info=_subscription_info(subscription),
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=settings.push_ttl_seconds,
            timeout=PUSH_TIMEOUT_SECONDS,
        )
        return True
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in GONE_STATUSES:
            # Permanently gone: the caller deletes the row.
            logger.info("push subscription %s is gone (%s)", subscription.id, status)
            raise
        logger.warning("push to subscription %s failed: %s", subscription.id, exc)
        return False
    except Exception as exc:  # noqa: BLE001 - a push must never break the request
        logger.warning("push to subscription %s errored: %s", subscription.id, exc)
        return False


def notify_user(db: Session, user_id: int, payload: dict[str, Any]) -> int:
    """Push ``payload`` to every browser ``user_id`` has subscribed.

    Returns the number of endpoints the push service accepted. Rows whose
    endpoint is permanently gone are removed here, so the table self-heals
    instead of retrying a dead endpoint on every future event.

    This makes blocking network calls and must not be called from an event
    loop; use ``schedule_notify`` there.
    """
    if not settings.push_configured:
        return 0

    subscriptions = list(
        db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all()
    )
    if not subscriptions:
        return 0

    delivered = 0
    for subscription in subscriptions:
        try:
            if send_to_subscription(subscription, payload):
                subscription.last_used_at = utcnow()
                delivered += 1
        except Exception:  # noqa: BLE001 - the endpoint is gone; prune it
            db.execute(delete(PushSubscription).where(PushSubscription.id == subscription.id))
    db.commit()
    return delivered


def _deliver(user_id: int, payload: dict[str, Any]) -> None:
    """Run one push batch on a pool worker with its own session.

    A fresh session is required because the caller's is request-scoped and will
    be closed as soon as the response is returned.
    """
    from .database import SessionLocal

    db = SessionLocal()
    try:
        notify_user(db, user_id, payload)
    except Exception as exc:  # noqa: BLE001 - a push is a side channel
        logger.warning("push to user %s errored: %s", user_id, exc)
    finally:
        db.close()


def message_payload(db: Session, message, conversation_id: int) -> dict[str, Any]:
    """The payload a service worker needs to render a message alert.

    Shaped like the realtime ``message`` frame so the service worker can reuse
    one rendering path for both transports. ``title``/``body`` are precomputed
    because a service worker has no session and cannot fetch the sender itself.
    """
    sender = db.get(User, message.sender_id)
    name = sender.display_name if sender is not None else "Someone"
    body = message.body or "Sent an attachment"
    return {
        "type": "message",
        "title": name,
        "body": body,
        "tag": f"dm-{conversation_id}",
        "url": f"/messages/{conversation_id}",
        "conversation_id": conversation_id,
        "message_id": message.id,
        "sender": {
            "id": sender.id if sender is not None else None,
            "username": sender.username if sender is not None else None,
            "display_name": name,
            "avatar_color": sender.avatar_color if sender is not None else "#6366f1",
        },
    }


def notification_payload(event: dict[str, Any], text: str, url: str) -> dict[str, Any]:
    """Wrap a realtime notification frame into a push payload.

    Takes the whole frame (``notification`` + ``actor``) rather than the inner
    notification, because the actor is what names the alert.
    """
    notification = event.get("notification") or {}
    actor = event.get("actor") or {}
    return {
        "type": "notification",
        "title": actor.get("display_name") or "Pulse",
        "body": text,
        "tag": f"notification-{notification.get('id')}",
        "url": url,
        "notification_id": notification.get("id"),
    }


def _main() -> None:  # pragma: no cover - developer utility
    """Print a fresh VAPID key pair for the environment.

    Run with ``python -m app.push``. The private key is a deployment secret:
    changing it invalidates every subscription already stored, because the
    browser subscribed against the old public key.
    """
    private, public = generate_keys()
    print(f"VAPID_PRIVATE_KEY={private}")
    print(f"VAPID_PUBLIC_KEY={public}")


if __name__ == "__main__":  # pragma: no cover - developer utility
    _main()
