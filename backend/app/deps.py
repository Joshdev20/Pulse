"""Shared FastAPI dependencies and serialization helpers."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Follow, Like, Media, Post, User
from .schemas import MediaOut, PostOut, UserPublic
from .security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]

MEDIA_URL = "/api/media/{id}"


def avatar_url_for(user: User) -> str | None:
    """URL of a user's uploaded picture, or None to fall back to initials."""
    if user.avatar_media_id is None:
        return None
    return MEDIA_URL.format(id=user.avatar_media_id)


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


def serialize_user(db: Session, user: User, viewer: User | None = None) -> UserPublic:
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
        created_at=user.created_at,
        follower_count=user.follower_count,
        following_count=user.following_count,
        post_count=post_count,
        is_following=is_following,
        is_admin=user.is_admin,
        is_verified=user.is_verified,
    )


def serialize_users(db: Session, users: list[User], viewer: User | None = None) -> dict[int, UserPublic]:
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
            created_at=user.created_at,
            follower_count=follower_counts.get(user.id, 0),
            following_count=following_counts.get(user.id, 0),
            post_count=post_counts.get(user.id, 0),
            is_following=user.id in following_ids,
            is_admin=user.is_admin,
            is_verified=user.is_verified,
        )
        for user in users
    }


def serialize_posts(db: Session, posts: list[Post], viewer: User | None = None) -> list[PostOut]:
    """Serialize posts, batching like/comment counts and viewer state."""
    if not posts:
        return []
    post_ids = [post.id for post in posts]

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

    liked_ids: set[int] = set()
    if viewer is not None:
        liked_ids = {
            row[0]
            for row in db.execute(
                select(Like.post_id).where(Like.user_id == viewer.id, Like.post_id.in_(post_ids))
            ).all()
        }

    users = serialize_users(db, [post.author for post in posts], viewer)

    return [
        PostOut(
            id=post.id,
            body=post.body,
            created_at=post.created_at,
            edited_at=post.edited_at,
            author=users[post.author_id],
            tags=post.tags,
            media=serialize_media(post.media),
            like_count=like_counts.get(post.id, 0),
            comment_count=comment_counts.get(post.id, 0),
            liked_by_me=post.id in liked_ids,
        )
        for post in posts
    ]
