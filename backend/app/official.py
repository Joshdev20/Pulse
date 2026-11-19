"""The official account and the follow every account owes it.

Announcements are only useful if they reach people, and a follow is what makes
an announcement arrive in the feed and in notifications. So the official account
is followed by every account: at registration, and by a backfill at startup for
accounts that predate the rule.

The rule is a product decision, not a security boundary. It is expressed as a
config flag so a deployment can turn it off without a code change, and every
helper here is a no-op when the flag is off or the account does not exist — a
fresh database with no `pulse` user must not fail to boot.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Follow, User


def official_account(db: Session) -> User | None:
    """The official account, or None when it is unset or does not exist."""
    name = (settings.official_account_username or "").strip()
    if not name:
        return None
    return db.scalar(select(User).where(func.lower(User.username) == name.lower()))


def is_official(user: User) -> bool:
    name = (settings.official_account_username or "").strip()
    return bool(name) and user.username.lower() == name.lower()


def ensure_official_follow(db: Session, user: User) -> bool:
    """Make ``user`` follow the official account. Returns True if a row was added.

    Does not commit: the caller owns the transaction, so registration can write
    the follow in the same commit as the account itself.
    """
    if not settings.official_follow_required:
        return False
    official = official_account(db)
    if official is None or official.id == user.id:
        return False
    exists = db.scalar(
        select(Follow).where(
            Follow.follower_id == user.id, Follow.following_id == official.id
        )
    )
    if exists is not None:
        return False
    db.add(Follow(follower_id=user.id, following_id=official.id))
    return True


def backfill_official_follows(db: Session) -> int:
    """Give every existing account the official follow. Returns rows added.

    Runs at startup so accounts created before the rule are covered. It is
    idempotent: the existence check in ``ensure_official_follow`` means a restart
    adds nothing, so this is safe to run on every boot.
    """
    if not settings.official_follow_required:
        return 0
    official = official_account(db)
    if official is None:
        return 0

    following = set(
        db.scalars(
            select(Follow.follower_id).where(Follow.following_id == official.id)
        ).all()
    )
    user_ids = db.scalars(select(User.id).where(User.id != official.id)).all()
    added = 0
    for user_id in user_ids:
        if user_id in following:
            continue
        db.add(Follow(follower_id=user_id, following_id=official.id))
        added += 1
    if added:
        db.commit()
    return added
