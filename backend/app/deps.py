"""Shared FastAPI dependencies and serialization helpers."""

import time
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .database import get_db
from .models import Follow, Like, Media, Post, User, utcnow
from .realtime import manager
from .schemas import MediaOut, PostOut, UserPublic
from .security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]

MEDIA_URL = "/api/media/{id}"


def online_user_ids() -> set[int]:
    """User ids holding an open realtime socket in this process.

    Read straight from the connection manager rather than the database, so
    "online" reflects a live socket rather than the last request. With more than
    one worker this is per-worker: a user connected to another worker reads as
    offline here. Redis-backed presence would be the fix, and the same
    single-node caveat already applies to the rate limiter.
    """
    return manager.online_ids()


def avatar_url_for(user: User) -> str | None:
    """URL of a user's uploaded picture, or None to fall back to initials."""
    if user.avatar_media_id is None:
        return None
    return MEDIA_URL.format(id=user.avatar_media_id)


def cover_url_for(user: User) -> str | None:
    """URL of a user's uploaded header image, or None for the plain gradient."""
    if user.cover_media_id is None:
        return None
    return MEDIA_URL.format(id=user.cover_media_id)


def serialize_media(items: list[Media]) -> list[MediaOut]:
    return [
        MediaOut(
            id=item.id,
            kind=item.kind,
            content_type=item.content_type,
            size_bytes=item.size_bytes,
            width=item.width,
            height=item.height,
            alt_text=item.alt_text,
            url=MEDIA_URL.format(id=item.id),
        )
        for item in items
    ]


def _user_from_token(token: str, db: Session) -> User | None:
    subject = decode_access_token(token)
    if subject is None:
        return None
    try:
        user_id = int(subject)
    except ValueError:
        return None
    return db.get(User, user_id)


# Last time each user's `last_seen_at` was written, in this process. Presence is
# shown to the minute, so writing it on every request would mean a database write
# per request for no visible gain. Per-process and reset on restart, which is
# fine: the value is a coarse timestamp, not a counter.
_LAST_SEEN_WRITTEN: dict[int, float] = {}
LAST_SEEN_THROTTLE_SECONDS = 60.0
# Bound on the table, for the same reason the rate limiter sweeps its buckets:
# entries are keyed by user id and would otherwise accumulate for the life of
# the process. Past this many users the oldest stamps are dropped, which at
# worst costs one extra write for the user who reappears.
MAX_LAST_SEEN_ENTRIES = 10_000


def reset_last_seen_tracking() -> None:
    """Forget every throttle stamp. Used by tests to isolate one from the next."""
    _LAST_SEEN_WRITTEN.clear()


def forget_last_seen(user_id: int) -> None:
    """Drop one user's throttle stamp so the next write is not suppressed.

    Used when a socket closes: that transition is the precise end of a presence
    window, so it must be recorded even if a request stamped the same user a
    moment earlier.
    """
    _LAST_SEEN_WRITTEN.pop(user_id, None)


def touch_last_seen(db: Session, user: User) -> None:
    """Record that ``user`` was active, at most once per throttle window.

    Kept off the write path when nothing changed: the update only runs when the
    in-process clock says the window has passed, so a burst of requests from one
    client costs one write rather than one per request.
    """
    now = time.monotonic()
    previous = _LAST_SEEN_WRITTEN.get(user.id)
    if previous is not None and now - previous < LAST_SEEN_THROTTLE_SECONDS:
        return
    if len(_LAST_SEEN_WRITTEN) >= MAX_LAST_SEEN_ENTRIES:
        # Insertion order is close enough to age order here; dropping the
        # oldest entries only ever costs a redundant write.
        for stale in list(_LAST_SEEN_WRITTEN)[: len(_LAST_SEEN_WRITTEN) // 4]:
            _LAST_SEEN_WRITTEN.pop(stale, None)
    _LAST_SEEN_WRITTEN[user.id] = now
    stamp = utcnow()
    # A targeted UPDATE rather than mutating the ORM object: this dependency
    # runs for every request, including ones whose handler never commits, and
    # we do not want to leave a dirty object on the session.
    db.execute(update(User).where(User.id == user.id).values(last_seen_at=stamp))
    db.commit()
    user.last_seen_at = stamp


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not credentials.credentials:
        raise unauthorized
    user = _user_from_token(credentials.credentials, db)
    if user is None or not user.is_active:
        raise unauthorized
    # A ban must invalidate tokens that were already issued, otherwise a banned
    # user stays signed in until their week-long token expires.
    if user.is_banned:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This account has been suspended",
        )
    # Any authenticated request is proof of activity, so this is the cheapest
    # correct place to keep `last_seen_at` current. Throttled internally.
    touch_last_seen(db, user)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_admin(user: CurrentUser) -> User:
    """Gate the moderation endpoints.

    Depends on ``get_current_user`` rather than repeating the token handling, so
    a banned or inactive account can never reach an admin route.
    """
    if not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Administrator access is required",
        )
    return user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


def presence_of(user: User, include: bool) -> dict[str, object]:
    """The presence fields for ``user``, or empty when they are out of scope.

    Presence is only meaningful to someone looking at a direct-message thread,
    so it is opt-in rather than part of every user payload. Leaving it on by
    default would broadcast who is online through search, follower lists and
    post authors, which is a different (and unasked-for) disclosure.
    """
    if not include:
        return {}
    return {"last_seen_at": user.last_seen_at, "is_online": user.id in online_user_ids()}


def serialize_user(
    db: Session, user: User, viewer: User | None = None, *, with_presence: bool = False
) -> UserPublic:
    post_count = db.scalar(select(func.count()).select_from(Post).where(Post.author_id == user.id)) or 0
    is_following = False
    if viewer is not None and viewer.id != user.id:
        is_following = (
            db.scalar(
                select(Follow.id).where(
                    Follow.follower_id == viewer.id, Follow.following_id == user.id
                )
            )
            is not None
        )
    return UserPublic(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        bio=user.bio,
        location=user.location,
        website=user.website,
        avatar_color=user.avatar_color,
        avatar_url=avatar_url_for(user),
        cover_url=cover_url_for(user),
        created_at=user.created_at,
        follower_count=user.follower_count,
        following_count=user.following_count,
        post_count=post_count,
        is_following=is_following,
        is_admin=user.is_admin,
        is_verified=user.is_verified,
        **presence_of(user, with_presence),
    )


def serialize_users(
    db: Session, users: list[User], viewer: User | None = None, *, with_presence: bool = False
) -> dict[int, UserPublic]:
    """Serialize many users with a single query per aggregate instead of one per user."""
    if not users:
        return {}
    ids = [user.id for user in users]

    post_counts = dict(
        db.execute(
            select(Post.author_id, func.count(Post.id)).where(Post.author_id.in_(ids)).group_by(Post.author_id)
        ).all()
    )
    follower_counts = dict(
        db.execute(
            select(Follow.following_id, func.count(Follow.id))
            .where(Follow.following_id.in_(ids))
            .group_by(Follow.following_id)
        ).all()
    )
    following_counts = dict(
        db.execute(
            select(Follow.follower_id, func.count(Follow.id))
            .where(Follow.follower_id.in_(ids))
            .group_by(Follow.follower_id)
        ).all()
    )

    following_ids: set[int] = set()
    if viewer is not None:
        following_ids = {
            row[0]
            for row in db.execute(
                select(Follow.following_id).where(
                    Follow.follower_id == viewer.id, Follow.following_id.in_(ids)
                )
            ).all()
        }

    # Presence is read once for the whole batch, and only when the caller asked
    # for it, so a follower list does not fan out a lookup per user.
    return {
        user.id: UserPublic(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            bio=user.bio,
            location=user.location,
            website=user.website,
            avatar_color=user.avatar_color,
            avatar_url=avatar_url_for(user),
            cover_url=cover_url_for(user),
            created_at=user.created_at,
            follower_count=follower_counts.get(user.id, 0),
            following_count=following_counts.get(user.id, 0),
            post_count=post_counts.get(user.id, 0),
            is_following=user.id in following_ids,
            is_admin=user.is_admin,
            is_verified=user.is_verified,
            **presence_of(user, with_presence),
        )
        for user in users
    }


def serialize_posts(db: Session, posts: list[Post], viewer: User | None = None) -> list[PostOut]:
    """Serialize posts, batching like/comment/repost counts and viewer state.

    A repost row carries no content of its own, so the post it shares is
    serialized alongside it and nested as ``repost_of``. The originals are
    included in every batched count query, so a repost and the original it
    points at agree on their like and comment totals.
    """
    if not posts:
        return []

    # Every post whose counts are needed: the rows themselves plus the
    # originals behind any repost (which may not be in `posts` at all).
    involved: dict[int, Post] = {}
    for post in posts:
        involved[post.id] = post
        if post.repost_of is not None:
            involved[post.repost_of.id] = post.repost_of
    post_ids = list(involved)

    like_counts = dict(
        db.execute(
            select(Like.post_id, func.count(Like.id)).where(Like.post_id.in_(post_ids)).group_by(Like.post_id)
        ).all()
    )
    from .models import Comment  # local import avoids a cycle at module import time

    comment_counts = dict(
        db.execute(
            select(Comment.post_id, func.count(Comment.id))
            .where(Comment.post_id.in_(post_ids))
            .group_by(Comment.post_id)
        ).all()
    )
    # How many times each of these posts has been shared.
    repost_counts = dict(
        db.execute(
            select(Post.repost_of_id, func.count(Post.id))
            .where(Post.repost_of_id.in_(post_ids))
            .group_by(Post.repost_of_id)
        ).all()
    )

    liked_ids: set[int] = set()
    reposted_ids: set[int] = set()
    if viewer is not None:
        liked_ids = {
            row[0]
            for row in db.execute(
                select(Like.post_id).where(Like.user_id == viewer.id, Like.post_id.in_(post_ids))
            ).all()
        }
        reposted_ids = {
            row[0]
            for row in db.execute(
                select(Post.repost_of_id).where(
                    Post.author_id == viewer.id, Post.repost_of_id.in_(post_ids)
                )
            ).all()
        }

    authors = serialize_users(db, [post.author for post in involved.values()], viewer)

    def build(post: Post, include_repost: bool) -> PostOut:
        return PostOut(
            id=post.id,
            body=post.body,
            created_at=post.created_at,
            edited_at=post.edited_at,
            author=authors[post.author_id],
            tags=post.tags,
            media=serialize_media(post.media),
            like_count=like_counts.get(post.id, 0),
            comment_count=comment_counts.get(post.id, 0),
            liked_by_me=post.id in liked_ids,
            repost_count=repost_counts.get(post.id, 0),
            reposted_by_me=post.id in reposted_ids,
            # Deliberately no further nesting: a repost always points at an
            # original, so this branch is taken at most once.
            repost_of=build(post.repost_of, False)
            if include_repost and post.repost_of is not None
            else None,
        )

    return [build(post, True) for post in posts]
