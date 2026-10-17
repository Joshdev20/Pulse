"""Profiles, follow graph, suggestions and platform stats."""

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

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


async def _set_profile_image(
    db: DbSession,
    user: User,
    file: UploadFile,
    *,
    field: str,
    label: str,
    alt: str,
) -> UserPublic:
    """Store an uploaded image and point ``field`` at it.

    Shared by the profile picture and cover photo, which differ only in which
    column they set. ``field`` is a literal chosen by the caller, never client
    input, so it cannot be used to write an arbitrary attribute.
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
            f"{label} must be a JPEG, PNG, GIF or WebP image",
        ) from None

    if sniffed.kind != "image":
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"{label} must be an image",
        )

    stored_name = storage.store(data, sniffed.extension)
    media = Media(
        owner_id=user.id,
        stored_name=stored_name,
        content_type=sniffed.content_type,
        kind="image",
        size_bytes=len(data),
        # Same reason as post uploads: the bytes go in the row so a profile
        # picture still resolves for every other account after the host's
        # ephemeral filesystem is wiped by a redeploy.
        blob=data,
        alt_text=f"{user.display_name}'s {alt}",
    )
    db.add(media)
    db.flush()

    previous_name = _drop_previous_image(db, getattr(user, field))
    setattr(user, field, media.id)
    db.commit()
    db.refresh(user)
    invalidate_user_reads()

    if previous_name:
        storage.delete(previous_name)
    return serialize_user(db, user, user)


def _clear_profile_image(db: DbSession, user: User, field: str) -> UserPublic:
    """Detach and delete the image in ``field``, reverting to the fallback look."""
    stored_name = _drop_previous_image(db, getattr(user, field))
    setattr(user, field, None)
    db.commit()
    db.refresh(user)
    invalidate_user_reads()

    if stored_name:
        storage.delete(stored_name)
    return serialize_user(db, user, user)


def _drop_previous_image(db: DbSession, media_id: int | None) -> str | None:
    """Queue the superseded upload for deletion and return its stored name.

    Only an image that is not also attached to a post or message is removed, so
    replacing a picture can never delete content the user still expects to see.
    """
    if media_id is None:
        return None
    previous = db.get(Media, media_id)
    if previous is None or previous.post_id is not None or previous.message_id is not None:
        return None
    stored_name = previous.stored_name
    db.delete(previous)
    return stored_name


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
    return await _set_profile_image(
        db, user, file, field="avatar_media_id", label="Profile pictures", alt="profile picture"
    )


@router.delete("/me/avatar", response_model=UserPublic)
def clear_avatar(db: DbSession, user: CurrentUser) -> UserPublic:
    """Revert to the generated initials avatar and drop the uploaded file."""
    return _clear_profile_image(db, user, "avatar_media_id")


@router.post("/me/cover", response_model=UserPublic)
async def upload_cover(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> UserPublic:
    """Set the signed-in user's profile header image."""
    return await _set_profile_image(
        db, user, file, field="cover_media_id", label="Cover photos", alt="cover photo"
    )


@router.delete("/me/cover", response_model=UserPublic)
def clear_cover(db: DbSession, user: CurrentUser) -> UserPublic:
    """Remove the header image and go back to the plain gradient."""
    return _clear_profile_image(db, user, "cover_media_id")


@router.patch("/{username}", response_model=UserPublic)
def update_user(username: str, payload: UserUpdate, db: DbSession, user: CurrentUser) -> UserPublic:
    target = _get_user_or_404(db, username)
    if target.id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own profile")

    changes = payload.model_dump(exclude_unset=True)
    avatar_media_id = changes.pop("avatar_media_id", None)
    cover_media_id = changes.pop("cover_media_id", None)
    for field, value in changes.items():
        if value is not None:
            setattr(target, field, value.strip() if isinstance(value, str) else value)

    if "avatar_media_id" in payload.model_fields_set:
        target.avatar_media_id = _validated_profile_image(
            db, user, avatar_media_id, "That image cannot be used as a profile picture"
        )
    if "cover_media_id" in payload.model_fields_set:
        target.cover_media_id = _validated_profile_image(
            db, user, cover_media_id, "That image cannot be used as a cover photo"
        )

    db.commit()
    db.refresh(target)
    invalidate_user_reads()
    return serialize_user(db, target, user)


def _validated_profile_image(db: DbSession, user: User, media_id: int | None, message: str) -> int | None:
    """Resolve a client-supplied media id to one this user may use as a picture.

    Only the caller's own upload, and only an image, so a profile cannot be
    pointed at someone else's attachment.
    """
    if media_id is None:
        return None
    media = db.get(Media, media_id)
    if media is None or media.owner_id != user.id or media.kind != "image":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message)
    return media.id


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
        try:
            # A double-tap can insert two follow rows at once; the unique
            # constraint on the pair rejects the loser, and following someone
            # twice is the same end state as following once, so it is a success.
            db.flush()
        except IntegrityError:
            db.rollback()
        else:
            # Local import avoids a cycle: `notifications` imports from `deps`.
            from .notifications import notification_event, push_notification

            event = notification_event(db, notification)
            await manager.publish(target.id, event)
            # The socket covers an open tab; Web Push reaches a closed browser.
            push_notification(target.id, event)
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
