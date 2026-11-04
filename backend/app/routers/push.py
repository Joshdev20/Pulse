"""Web Push subscription management and a key-generation helper.

The browser subscribes against the push service itself; this router only records
the resulting endpoint so the server can use it later. The public key is exposed
so a client can subscribe without the key being baked into the bundle — the
bundle is built once and the key belongs to the deployment, not the build.

A browser can be signed into several accounts at once. The endpoint is stored
once and *linked* to each account that subscribes on it, so switching accounts in
the same browser never cuts off the account that is not in front. See
``PushSubscriptionUser``.
"""

import base64
import binascii

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import branding
from ..config import settings
from ..deps import CurrentUser, DbSession
from ..models import PushSubscription, PushSubscriptionUser, User, utcnow

router = APIRouter(prefix="/api/push", tags=["push"])

# Browsers cap these well below the column widths; the bound is here so a
# malicious client cannot write an unbounded string.
MAX_ENDPOINT = 2000
MAX_KEY = 255


class PushKeys(BaseModel):
    """The subscription keys a browser produces (`toJSON()` of a subscription)."""

    p256dh: str = Field(min_length=1, max_length=MAX_KEY)
    auth: str = Field(min_length=1, max_length=MAX_KEY)


class PushSubscribe(BaseModel):
    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT)
    keys: PushKeys


class PushStatus(BaseModel):
    enabled: bool
    public_key: str | None
    devices: int
    # Whether the caller's own browser endpoint is still linked to this account.
    # `None` when the client did not say which endpoint it holds. `False` means
    # the link is gone (it was pruned after a VAPID key rotation, or the account
    # was signed out on this browser), so the client must register again. Without
    # this the settings toggle reads "subscribed" from the browser's own
    # subscription while the server has nothing to send to for this account.
    endpoint_registered: bool | None = None


class PushUnsubscribe(BaseModel):
    """Unsubscribing needs only the endpoint: the keys are not used to send."""

    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT)
    # When true, the whole endpoint is dropped rather than just this account's
    # link to it. The push switch turning off means "stop sending this browser
    # anything", so the endpoint itself goes.
    all: bool = False


def _valid_key(value: str) -> bool:
    """Whether a key is base64url that decodes to something plausible.

    A malformed key is rejected at subscribe time rather than failing at send
    time, where the only symptom is a push service returning an error for every
    future alert.
    """
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return False
    return len(raw) >= 16


@router.get("/status", response_model=PushStatus)
def push_status(
    db: DbSession,
    user: CurrentUser,
    endpoint: str | None = Query(default=None, max_length=MAX_ENDPOINT),
) -> PushStatus:
    """Whether push is available, its public key, and this user's device count.

    ``endpoint`` is optional and, when given, also reports whether that specific
    endpoint is still registered to this user. The caller is a browser that holds
    the endpoint, so it is the only party that can tell a pruned row from a live
    one and re-subscribe.
    """
    devices = len(
        db.scalars(
            select(PushSubscription.id)
            .join(PushSubscriptionUser, PushSubscriptionUser.subscription_id == PushSubscription.id)
            .where(PushSubscriptionUser.user_id == user.id)
        ).all()
    )
    registered: bool | None = None
    if endpoint:
        registered = (
            db.scalar(
                select(PushSubscriptionUser.id)
                .join(PushSubscription, PushSubscription.id == PushSubscriptionUser.subscription_id)
                .where(
                    PushSubscription.endpoint == endpoint,
                    PushSubscriptionUser.user_id == user.id,
                )
            )
            is not None
        )
    return PushStatus(
        enabled=settings.push_configured,
        public_key=settings.valid_vapid_public_key,
        devices=devices,
        endpoint_registered=registered,
    )


@router.get("/icon/{user_id}.png", include_in_schema=False)
def notification_icon(user_id: int, db: DbSession) -> Response:
    """The branded icon a notification for this actor is shown with.

    The sender's avatar with the Pulse mark set into the corner, so an alert
    shows both who it is about and where it came from. The push payload points
    at this URL; the service worker hands the OS a fully-qualified version of it.

    Public and unauthenticated on purpose. The image is fetched by the operating
    system's notification daemon when the alert is drawn, which holds no session
    and may run while the browser is closed — the whole case push exists for. It
    exposes no more than the avatar route already serves without a token (see
    ``routers/media.py``), and only the composed square is produced here, so no
    arbitrary media id can be reached through it.

    Cached hard because the bytes change only when the avatar does, and a
    notification is a bad time to wait on a round trip. The avatar's own
    ``updated_at`` is not part of the URL, so a changed picture can lag by the
    cache lifetime; that is a fair trade for an icon that is otherwise fetched on
    every single alert.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    try:
        image = branding.notification_icon(db, user)
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Notification icons are unavailable: Pillow is not installed",
        ) from None
    return Response(
        content=image,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600, s-maxage=86400",
            "Content-Length": str(len(image)),
        },
    )


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(
    payload: PushSubscribe, request: Request, db: DbSession, user: CurrentUser
) -> dict[str, int]:
    """Link this browser's push endpoint to the current account.

    The endpoint is stored once and *linked* to every account that subscribes on
    it. Signing into a second account in the same browser therefore adds a link
    rather than reassigning the endpoint — which is what previously cut the first
    account off, exactly when its alerts mattered most (you are not looking at it).
    """
    if not settings.push_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Push notifications are not configured"
        )
    if not payload.endpoint.startswith("https://"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Endpoint must be an https URL")
    if not _valid_key(payload.keys.p256dh) or not _valid_key(payload.keys.auth):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid subscription keys")

    existing = db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    if existing is None:
        # `user_id` records the account that first registered this endpoint. It
        # predates multi-account support and is NOT NULL; the delivery set is the
        # link table below, which every subscribing account is added to.
        existing = PushSubscription(endpoint=payload.endpoint, user_id=user.id)
        db.add(existing)
    # Re-subscribing is the normal path after a key rotation, so the keys and the
    # device label are always refreshed. The owner is *not* reassigned: the link
    # table below records which accounts this endpoint serves.
    existing.p256dh = payload.keys.p256dh
    existing.auth = payload.keys.auth
    existing.user_agent = (request.headers.get("user-agent") or "")[:200]
    existing.last_used_at = utcnow()
    db.flush()

    link = db.scalar(
        select(PushSubscriptionUser).where(
            PushSubscriptionUser.subscription_id == existing.id,
            PushSubscriptionUser.user_id == user.id,
        )
    )
    if link is None:
        db.add(PushSubscriptionUser(subscription_id=existing.id, user_id=user.id))
    db.commit()

    devices = len(
        db.scalars(
            select(PushSubscription.id)
            .join(PushSubscriptionUser, PushSubscriptionUser.subscription_id == PushSubscription.id)
            .where(PushSubscriptionUser.user_id == user.id)
        ).all()
    )
    return {"devices": devices}


@router.post("/unsubscribe")
def unsubscribe(payload: PushUnsubscribe, db: DbSession, user: CurrentUser) -> dict[str, int]:
    """Stop pushing to an endpoint.

    By default only this account's link is removed, so signing out of one account
    on a shared browser leaves the other account's alerts intact. With ``all``
    set — the user switching push off — the endpoint itself is deleted, because
    that choice means "nothing from this browser".
    """
    subscription = db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    if subscription is not None:
        if payload.all:
            # Scoped through the caller's own link, so one account cannot delete
            # an endpoint that belongs only to somebody else.
            owned = db.scalar(
                select(PushSubscriptionUser.id).where(
                    PushSubscriptionUser.subscription_id == subscription.id,
                    PushSubscriptionUser.user_id == user.id,
                )
            )
            if owned is not None:
                db.execute(
                    delete(PushSubscription).where(PushSubscription.id == subscription.id)
                )
        else:
            db.execute(
                delete(PushSubscriptionUser).where(
                    PushSubscriptionUser.subscription_id == subscription.id,
                    PushSubscriptionUser.user_id == user.id,
                )
            )
    db.commit()
    devices = len(
        db.scalars(
            select(PushSubscription.id)
            .join(PushSubscriptionUser, PushSubscriptionUser.subscription_id == PushSubscription.id)
            .where(PushSubscriptionUser.user_id == user.id)
        ).all()
    )
    return {"devices": devices}
