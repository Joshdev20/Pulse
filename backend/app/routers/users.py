"""Profiles, follow graph, suggestions and platform stats."""

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select

from .. import cache, storage
from ..config import settings
from ..deps import CurrentUser, DbSession, serialize_posts, serialize_user, serialize_users
from ..models import Comment, Follow, Like, Media, Notification, Post, User
from ..realtime import manager
from ..schemas import Page, SuggestedUser, UserPublic, UserUpdate, Stats

router = APIRouter(prefix="/api/users", tags=["users"])

# A follow or profile edit changes follower counts, suggestions and the feed's
# author list, so all of those cached reads are invalidated together.
USER_WRITE_NAMESPACES = ("users", "suggested", "feed", "posts", "stats", "search")


def invalidate_user_reads() -> None:
    cache.bump(*USER_WRITE_NAMESPACES)


def _get_user_or_404(db: DbSession, username: str) -> User:
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/suggested", response_model=list[SuggestedUser])
def suggested_users(
    db: DbSession, viewer: CurrentUser, limit: int = Query(3, ge=1, le=20)
) -> list[SuggestedUser]:
    """Suggest accounts the viewer does not yet follow, preferring popular ones."""

    def produce() -> list[dict]:
        exclude_ids: set[int] = {viewer.id} | {
            row[0]
            for row in db.execute(
                select(Follow.following_id).where(Follow.follower_id == viewer.id)
            ).all()
        }

        follower_counts = dict(
            db.execute(
                select(Follow.following_id, func.count(Follow.id)).group_by(Follow.following_id)
            ).all()
        )

        query = select(User).where(User.is_active.is_(True)).where(User.id.notin_(exclude_ids))
        candidates = list(db.scalars(query).all())
        candidates.sort(key=lambda u: (-follower_counts.get(u.id, 0), u.username))

        picked = candidates[:limit]
        serialized = serialize_users(db, picked, viewer)
        out: list[dict] = []
        for user in picked:
            followers = follower_counts.get(user.id, 0)
            reason = (
                f"{followers} follower{'s' if followers != 1 else ''}" if followers else "New on Pulse"
            )
            out.append(
                SuggestedUser(user=serialized[user.id], reason=reason).model_dump(mode="json")
            )
        return out

    return cache.get_or_set(
        "suggested", settings.cache_profile_ttl_seconds, (viewer.id, limit), produce
    )


@router.get("/stats", response_model=Stats)
def platform_stats(db: DbSession, viewer: CurrentUser) -> Stats:
    def produce() -> dict:
        def count(model) -> int:
            return db.scalar(select(func.count()).select_from(model)) or 0

        return Stats(
            users=count(User), posts=count(Post), likes=count(Like), comments=count(Comment)
        ).model_dump(mode="json")

    return cache.get_or_set("stats", settings.cache_stats_ttl_seconds, (), produce)


@router.get("/{username}", response_model=UserPublic)
def get_user(username: str, db: DbSession, viewer: CurrentUser) -> UserPublic:
    return cache.get_or_set(
        "users",
        settings.cache_profile_ttl_seconds,
        (viewer.id, username.lower()),
        lambda: serialize_user(db, _get_user_or_404(db, username), viewer).model_dump(mode="json"),
    )


@router.post("/me/avatar", response_model=UserPublic)
async def upload_avatar(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> UserPublic:
    """Set the signed-in user's profile picture.

    The upload is sniffed and stored through the same path as post media, so
    only real image bytes are accepted regardless of the filename or the
    declared content type.
    """
    data = await file.read(storage.MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(data) > storage.MAX_IMAGE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Images are limited to {storage.MAX_IMAGE_BYTES // (1024 * 1024)}MB",
        )

    try:
        sniffed = storage.sniff(data[:64])
    except storage.UnsupportedMedia:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Profile pictures must be a JPEG, PNG, GIF or WebP image",
        ) from None

    if sniffed.kind != "image":
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Profile pictures must be an image",
        )

    stored_name = storage.store(data, sniffed.extension)
    media = Media(
        owner_id=user.id,
        stored_name=stored_name,
        content_type=sniffed.content_type,
        kind="image",
        size_bytes=len(data),
        alt_text=f"{user.display_name}'s profile picture",
    )
    db.add(media)
    db.flush()

    previous_id = user.avatar_media_id
    previous_name = None
    if previous_id is not None:
        previous = db.get(Media, previous_id)
        # Only the uploader's own picture, and only one that is not also
        # attached to a post or message, may be cleaned up.
        if previous is not None and previous.post_id is None and previous.message_id is None:
            previous_name = previous.stored_name
            db.delete(previous)

    user.avatar_media_id = media.id
    db.commit()
    db.refresh(user)
    invalidate_user_reads()

    if previous_name:
        storage.delete(previous_name)
    return serialize_user(db, user, user)


@router.delete("/me/avatar", response_model=UserPublic)
def clear_avatar(db: DbSession, user: CurrentUser) -> UserPublic:
    """Revert to the generated initials avatar and drop the uploaded file."""
    previous_id = user.avatar_media_id
    user.avatar_media_id = None
    db.flush()

    stored_name = None
    if previous_id is not None:
        previous = db.get(Media, previous_id)
        if previous is not None and previous.post_id is None and previous.message_id is None:
            stored_name = previous.stored_name
            db.delete(previous)

    db.commit()
    db.refresh(user)
    invalidate_user_reads()

    if stored_name:
        storage.delete(stored_name)
    return serialize_user(db, user, user)


@router.patch("/{username}", response_model=UserPublic)
def update_user(username: str, payload: UserUpdate, db: DbSession, user: CurrentUser) -> UserPublic:
    target = _get_user_or_404(db, username)
    if target.id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own profile")

    changes = payload.model_dump(exclude_unset=True)
    avatar_media_id = changes.pop("avatar_media_id", None)
    for field, value in changes.items():
        if value is not None:
            setattr(target, field, value.strip() if isinstance(value, str) else value)

    if "avatar_media_id" in payload.model_fields_set:
        if avatar_media_id is None:
            target.avatar_media_id = None
        else:
            media = db.get(Media, avatar_media_id)
            # Only the caller's own upload may be attached, and only an image,
            # so a profile cannot be pointed at someone else's attachment.
            if media is None or media.owner_id != user.id or media.kind != "image":
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "That image cannot be used as a profile picture"
                )
            target.avatar_media_id = media.id

    db.commit()
    db.refresh(target)
    invalidate_user_reads()
    return serialize_user(db, target, user)


@router.get("/{username}/posts", response_model=Page)
def user_posts(
    username: str,
    db: DbSession,
    viewer: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> Page:
    def produce() -> dict:
        target = _get_user_or_404(db, username)
        query = (
            select(Post)
            .where(Post.author_id == target.id)
            .order_by(Post.created_at.desc(), Post.id.desc())
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        posts = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all())
        return Page(
            items=serialize_posts(db, posts, viewer),
            total=total,
            page=page,
            page_size=page_size,
            has_more=page * page_size < total,
        ).model_dump(mode="json")

    return cache.get_or_set(
        "posts", settings.cache_ttl_seconds, (viewer.id, "user", username.lower(), page, page_size), produce
    )


@router.get("/{username}/likes", response_model=Page)
def user_likes(
    username: str,
    db: DbSession,
    viewer: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> Page:
    def produce() -> dict:
        target = _get_user_or_404(db, username)
        query = (
            select(Post)
            .join(Like, Like.post_id == Post.id)
            .where(Like.user_id == target.id)
            .order_by(Like.created_at.desc())
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        posts = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all())
        return Page(
            items=serialize_posts(db, posts, viewer),
            total=total,
            page=page,
            page_size=page_size,
            has_more=page * page_size < total,
        ).model_dump(mode="json")

    return cache.get_or_set(
        "posts", settings.cache_ttl_seconds, (viewer.id, "likes", username.lower(), page, page_size), produce
    )


@router.post("/{username}/follow", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def follow_user(username: str, db: DbSession, user: CurrentUser) -> UserPublic:
    target = _get_user_or_404(db, username)
    if target.id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot follow yourself")
    existing = db.scalar(
        select(Follow).where(Follow.follower_id == user.id, Follow.following_id == target.id)
    )
    if existing is None:
        db.add(Follow(follower_id=user.id, following_id=target.id))
        notification = Notification(user_id=target.id, actor_id=user.id, kind="follow")
        db.add(notification)
        db.flush()
        await manager.publish(
            target.id,
            {
                "type": "notification",
                "notification": {
                    "id": notification.id,
                    "kind": "follow",
                    "read": False,
                    "created_at": notification.created_at,
                    "post_id": None,
                    "actor_id": user.id,
                },
            },
        )
        db.commit()
        db.refresh(target)
        invalidate_user_reads()
    return serialize_user(db, target, user)


@router.delete("/{username}/follow", response_model=UserPublic)
def unfollow_user(username: str, db: DbSession, user: CurrentUser) -> UserPublic:
    target = _get_user_or_404(db, username)
    existing = db.scalar(
        select(Follow).where(Follow.follower_id == user.id, Follow.following_id == target.id)
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        db.refresh(target)
        invalidate_user_reads()
    return serialize_user(db, target, user)


@router.get("/{username}/followers", response_model=list[UserPublic])
def followers(username: str, db: DbSession, viewer: CurrentUser) -> list[UserPublic]:
    def produce() -> list[dict]:
        target = _get_user_or_404(db, username)
        users = [follow.follower for follow in target.followers]
        serialized = serialize_users(db, users, viewer)
        return [serialized[u.id].model_dump(mode="json") for u in users]

    return cache.get_or_set(
        "users", settings.cache_profile_ttl_seconds, (viewer.id, "followers", username.lower()), produce
    )


@router.get("/{username}/following", response_model=list[UserPublic])
def following(username: str, db: DbSession, viewer: CurrentUser) -> list[UserPublic]:
    def produce() -> list[dict]:
        target = _get_user_or_404(db, username)
        users = [follow.following for follow in target.following]
        serialized = serialize_users(db, users, viewer)
        return [serialized[u.id].model_dump(mode="json") for u in users]

    return cache.get_or_set(
        "users", settings.cache_profile_ttl_seconds, (viewer.id, "following", username.lower()), produce
    )
