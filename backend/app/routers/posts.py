"""Post CRUD, feed, likes and comments."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from .. import cache
from ..config import settings
from ..deps import (
    CurrentUser,
    DbSession,
    serialize_posts,
    serialize_users,
)
from ..models import Comment, Follow, Like, Media, Notification, Post, User, utcnow
from ..realtime import manager
from ..schemas import (
    CommentCreate,
    CommentOut,
    Page,
    PostCreate,
    PostOut,
    PostUpdate,
    UserPublic,
)
from ..tagging import extract_tag_names, resolve_tags

router = APIRouter(prefix="/api", tags=["posts"])

MAX_PAGE_SIZE = 50
MAX_ATTACHMENTS = 4

# Namespaces whose cached reads a post write invalidates. `users` carries the
# post counts and `trending`/`search`/`comments` the aggregates and lists that a
# new post, like or comment changes.
POST_WRITE_NAMESPACES = ("posts", "feed", "trending", "search", "comments", "users")


def invalidate_post_reads() -> None:
    cache.bump(*POST_WRITE_NAMESPACES)


def _get_post_or_404(db: DbSession, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    return post


def _claim_media(db: DbSession, media_ids: list[int], user: User) -> list[Media]:
    """Attach the caller's own unlinked uploads to a new post.

    Refusing files owned by someone else, or already attached elsewhere, keeps
    an upload id from being reused to graft another user's content into a post.
    """
    if not media_ids:
        return []
    unique = list(dict.fromkeys(media_ids))[:MAX_ATTACHMENTS]
    items = list(db.scalars(select(Media).where(Media.id.in_(unique))).all())
    if len(items) != len(unique):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown attachment")
    for item in items:
        if item.owner_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot attach this file")
        if item.post_id is not None or item.message_id is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Attachment already in use")
    return items


async def _notify(db: DbSession, notification: Notification, recipient_id: int) -> None:
    """Persist a notification and push it to the recipient if they are online."""
    db.add(notification)
    db.flush()
    await manager.publish(
        recipient_id,
        {
            "type": "notification",
            "notification": {
                "id": notification.id,
                "kind": notification.kind,
                "read": False,
                "created_at": notification.created_at or utcnow(),
                "post_id": notification.post_id,
                "actor_id": notification.actor_id,
            },
        },
    )


def _paginate(db: DbSession, query, page: int, page_size: int, viewer: User | None) -> Page:
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    posts = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = serialize_posts(db, list(posts), viewer)
    return Page(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.get("/feed", response_model=Page)
def feed(
    db: DbSession,
    viewer: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    scope: str = Query("following", pattern="^(following|all)$"),
) -> Page:
    """The home timeline: posts from followed accounts, or everything when scope=all."""

    def produce() -> dict:
        query = select(Post).order_by(Post.created_at.desc(), Post.id.desc())

        if scope == "following":
            author_ids = [viewer.id] + [
                row[0]
                for row in db.execute(
                    select(Follow.following_id).where(Follow.follower_id == viewer.id)
                ).all()
            ]
            query = query.where(Post.author_id.in_(author_ids))

        return _paginate(db, query, page, page_size, viewer).model_dump(mode="json")

    return cache.get_or_set(
        "feed",
        settings.cache_ttl_seconds,
        (viewer.id, page, page_size, scope),
        produce,
    )


@router.get("/posts", response_model=Page)
def list_posts(
    db: DbSession,
    viewer: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
) -> Page:
    return cache.get_or_set(
        "posts",
        settings.cache_ttl_seconds,
        (viewer.id, page, page_size),
        lambda: _paginate(
            db,
            select(Post).order_by(Post.created_at.desc(), Post.id.desc()),
            page,
            page_size,
            viewer,
        ).model_dump(mode="json"),
    )


@router.post("/posts", response_model=PostOut, status_code=status.HTTP_201_CREATED)
async def create_post(payload: PostCreate, db: DbSession, user: CurrentUser) -> PostOut:
    body = payload.body.strip()
    attachments = _claim_media(db, payload.media_ids, user)
    if not body and not attachments:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A post needs text or an attachment")

    post = Post(author_id=user.id, body=body)
    post.tags = resolve_tags(db, extract_tag_names(body))
    db.add(post)
    db.flush()
    for item in attachments:
        item.post_id = post.id
    db.commit()
    db.refresh(post)
    invalidate_post_reads()
    return serialize_posts(db, [post], user)[0]


@router.get("/posts/{post_id}", response_model=PostOut)
def get_post(post_id: int, db: DbSession, viewer: CurrentUser) -> PostOut:
    return cache.get_or_set(
        "posts",
        settings.cache_ttl_seconds,
        (viewer.id, post_id),
        lambda: serialize_posts(db, [_get_post_or_404(db, post_id)], viewer)[0].model_dump(mode="json"),
    )


@router.patch("/posts/{post_id}", response_model=PostOut)
def update_post(post_id: int, payload: PostUpdate, db: DbSession, user: CurrentUser) -> PostOut:
    post = _get_post_or_404(db, post_id)
    if post.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own posts")
    post.body = payload.body.strip()
    post.edited_at = utcnow()
    post.tags = resolve_tags(db, extract_tag_names(post.body))
    db.commit()
    db.refresh(post)
    invalidate_post_reads()
    return serialize_posts(db, [post], user)[0]


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: int, db: DbSession, user: CurrentUser) -> None:
    post = _get_post_or_404(db, post_id)
    if post.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own posts")
    db.delete(post)
    db.commit()
    invalidate_post_reads()


@router.post("/posts/{post_id}/like", response_model=PostOut)
async def like_post(post_id: int, db: DbSession, user: CurrentUser) -> PostOut:
    post = _get_post_or_404(db, post_id)
    existing = db.scalar(select(Like).where(Like.post_id == post.id, Like.user_id == user.id))
    if existing is None:
        db.add(Like(post_id=post.id, user_id=user.id))
        if post.author_id != user.id:
            await _notify(
                db,
                Notification(user_id=post.author_id, actor_id=user.id, kind="like", post_id=post.id),
                post.author_id,
            )
        db.commit()
        db.refresh(post)
    invalidate_post_reads()
    return serialize_posts(db, [post], user)[0]


@router.delete("/posts/{post_id}/like", response_model=PostOut)
def unlike_post(post_id: int, db: DbSession, user: CurrentUser) -> PostOut:
    post = _get_post_or_404(db, post_id)
    existing = db.scalar(select(Like).where(Like.post_id == post.id, Like.user_id == user.id))
    if existing is not None:
        db.delete(existing)
        db.commit()
        db.refresh(post)
    invalidate_post_reads()
    return serialize_posts(db, [post], user)[0]


@router.get("/posts/{post_id}/comments", response_model=list[CommentOut])
def list_comments(post_id: int, db: DbSession, viewer: CurrentUser) -> list[CommentOut]:
    def produce() -> list[dict]:
        _get_post_or_404(db, post_id)
        comments = db.scalars(
            select(Comment).where(Comment.post_id == post_id).order_by(Comment.created_at.asc())
        ).all()
        authors = serialize_users(db, [comment.author for comment in comments])
        return [
            CommentOut(
                id=comment.id,
                body=comment.body,
                created_at=comment.created_at,
                author=authors[comment.author_id],
            ).model_dump(mode="json")
            for comment in comments
        ]

    return cache.get_or_set("comments", settings.cache_ttl_seconds, (post_id,), produce)


@router.post("/posts/{post_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
async def create_comment(post_id: int, payload: CommentCreate, db: DbSession, user: CurrentUser) -> CommentOut:
    post = _get_post_or_404(db, post_id)
    comment = Comment(post_id=post.id, author_id=user.id, body=payload.body.strip())
    db.add(comment)
    if post.author_id != user.id:
        await _notify(
            db,
            Notification(user_id=post.author_id, actor_id=user.id, kind="comment", post_id=post.id),
            post.author_id,
        )
    db.commit()
    db.refresh(comment)
    invalidate_post_reads()
    author = serialize_users(db, [user])[user.id]
    return CommentOut(id=comment.id, body=comment.body, created_at=comment.created_at, author=author)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: int, db: DbSession, user: CurrentUser) -> None:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    # The comment author or the post author may remove a comment.
    if comment.author_id != user.id and comment.post.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot delete this comment")
    db.delete(comment)
    db.commit()
    invalidate_post_reads()


@router.get("/trending/tags", response_model=list[dict])
def trending_tags(db: DbSession, viewer: CurrentUser, limit: int = Query(10, ge=1, le=50)) -> list[dict]:
    from ..models import Tag, post_tags

    def produce() -> list[dict]:
        rows = db.execute(
            select(Tag.name, func.count(post_tags.c.post_id).label("uses"))
            .join(post_tags, post_tags.c.tag_id == Tag.id)
            .group_by(Tag.name)
            .order_by(func.count(post_tags.c.post_id).desc(), Tag.name.asc())
            .limit(limit)
        ).all()
        return [{"name": name, "uses": uses} for name, uses in rows]

    return cache.get_or_set("trending", settings.cache_ttl_seconds, ("tags", limit), produce)


@router.get("/trending/people", response_model=list[UserPublic])
def trending_people(db: DbSession, viewer: CurrentUser, limit: int = Query(5, ge=1, le=20)) -> list[UserPublic]:
    def produce() -> list[dict]:
        rows = db.execute(
            select(User, func.count(Like.id).label("likes"))
            .join(Post, Post.author_id == User.id)
            .join(Like, Like.post_id == Post.id)
            .group_by(User.id)
            .order_by(func.count(Like.id).desc())
            .limit(limit)
        ).all()
        users = [row[0] for row in rows]
        users = [u for u in users if u.id != viewer.id]
        serialized = serialize_users(db, users, viewer)
        return [serialized[u.id].model_dump(mode="json") for u in users]

    return cache.get_or_set("trending", settings.cache_ttl_seconds, ("people", viewer.id, limit), produce)
