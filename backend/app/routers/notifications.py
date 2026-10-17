"""Notification listing and read state."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..deps import CurrentUser, DbSession, avatar_url_for, serialize_users
from ..models import Notification, User, utcnow
from ..schemas import NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def notification_event(db: Session, notification: Notification) -> dict:
    """Build the realtime frame for a freshly created notification.

    Carries a small ``actor`` summary alongside the notification itself. The
    client shows this as a desktop popup while the tab is in the background,
    where it has no other way to name who did what — without it the popup could
    only say "someone liked your post". Deliberately a few fields rather than a
    full user object, to keep the frame small and avoid the extra count queries
    that ``serialize_user`` would run for a value that is only read for display.
    """
    actor = db.get(User, notification.actor_id) if notification.actor_id else None
    return {
        "type": "notification",
        "notification": {
            "id": notification.id,
            "kind": notification.kind,
            "read": False,
            "created_at": notification.created_at or utcnow(),
            "post_id": notification.post_id,
            "actor_id": notification.actor_id,
        },
        "actor": (
            {
                "id": actor.id,
                "username": actor.username,
                "display_name": actor.display_name,
                "avatar_color": actor.avatar_color,
                "avatar_url": avatar_url_for(actor),
            }
            if actor is not None
            else None
        ),
    }


def _serialize(db: DbSession, notifications: list[Notification]) -> list[NotificationOut]:
    actors = serialize_users(db, [n.actor for n in notifications if n.actor is not None])
    return [
        NotificationOut(
            id=n.id,
            kind=n.kind,
            read=n.read,
            created_at=n.created_at,
            post_id=n.post_id,
            actor=actors.get(n.actor_id) if n.actor_id else None,
        )
        for n in notifications
    ]


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    unread_only: bool = False,
) -> list[NotificationOut]:
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read.is_(False))
    notifications = list(
        db.scalars(query.order_by(Notification.created_at.desc()).limit(limit)).all()
    )
    return _serialize(db, notifications)


@router.get("/unread-count")
def unread_count(db: DbSession, user: CurrentUser) -> dict[str, int]:
    count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read.is_(False))
    )
    return {"count": count or 0}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, db: DbSession, user: CurrentUser) -> NotificationOut:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    notification.read = True
    db.commit()
    db.refresh(notification)
    return _serialize(db, [notification])[0]


@router.post("/read-all")
def mark_all_read(db: DbSession, user: CurrentUser) -> dict[str, int]:
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read.is_(False))
        .values(read=True)
    )
    db.commit()
    return {"updated": result.rowcount or 0}
