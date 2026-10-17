"""Web Push subscription management and a key-generation helper.

The browser subscribes against the push service itself; this router only records
the resulting endpoint so the server can use it later. The public key is exposed
so a client can subscribe without the key being baked into the bundle — the
bundle is built once and the key belongs to the deployment, not the build.
"""

import base64
import binascii

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..config import settings
from ..deps import CurrentUser, DbSession
from ..models import PushSubscription, utcnow

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


class PushUnsubscribe(BaseModel):
    """Unsubscribing needs only the endpoint: the keys are not used to send."""

    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT)


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
def push_status(db: DbSession, user: CurrentUser) -> PushStatus:
    """Whether push is available, its public key, and this user's device count."""
    devices = len(
        db.scalars(select(PushSubscription.id).where(PushSubscription.user_id == user.id)).all()
    )
    return PushStatus(
        enabled=settings.push_configured,
        public_key=settings.valid_vapid_public_key,
        devices=devices,
    )


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(
    payload: PushSubscribe, request: Request, db: DbSession, user: CurrentUser
) -> dict[str, int]:
    """Record (or refresh) this browser's push endpoint for the current user."""
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
        existing = PushSubscription(endpoint=payload.endpoint)
        db.add(existing)
    # Re-subscribing is the normal path after a key rotation, and a shared
    # browser can be signed into a different account than last time, so the
    # owner and keys are always overwritten rather than left stale.
    existing.user_id = user.id
    existing.p256dh = payload.keys.p256dh
    existing.auth = payload.keys.auth
    existing.user_agent = (request.headers.get("user-agent") or "")[:200]
    existing.last_used_at = utcnow()
    db.commit()

    devices = len(
        db.scalars(select(PushSubscription.id).where(PushSubscription.user_id == user.id)).all()
    )
    return {"devices": devices}


@router.post("/unsubscribe")
def unsubscribe(payload: PushUnsubscribe, db: DbSession, user: CurrentUser) -> dict[str, int]:
    """Forget an endpoint. Scoped to the caller, so it cannot drop another's."""
    db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == payload.endpoint,
            PushSubscription.user_id == user.id,
        )
    )
    db.commit()
    devices = len(
        db.scalars(select(PushSubscription.id).where(PushSubscription.user_id == user.id)).all()
    )
    return {"devices": devices}


def _main() -> None:  # pragma: no cover - developer utility
    """Print a fresh VAPID key pair for the environment.

    Run with ``python -m app.push``. The private key is a deployment secret:
    changing it invalidates every subscription already stored, because the
    browser subscribed against the old public key.
    """
    from ..push import generate_keys

    private, public = generate_keys()
    print(f"VAPID_PRIVATE_KEY={private}")
    print(f"VAPID_PUBLIC_KEY={public}")


if __name__ == "__main__":  # pragma: no cover - developer utility
    _main()
