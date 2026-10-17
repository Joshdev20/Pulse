"""Post CRUD, feed, likes and comments."""

import hashlib
import math
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from .. import cache
from ..config import settings
from ..deps import (
    CurrentUser,
    DbSession,
    serialize_posts,
    serialize_users,
)
from ..models import (
    Comment,
    CommentLike,
    Follow,
    Like,
    Media,
    Notification,
    Post,
    User,
    utcnow,
)
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

# How many recent posts the home feed considers before ordering them. The pool
# is bounded on purpose: ordering every post in the table would make the feed
# slower with every post ever written. A few hundred is enough to feel varied
# while keeping the cost of a feed request flat as the site grows.
FEED_POOL_SIZE = 300

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
    # Local import: `notifications` imports from `deps`, which would otherwise
    # make this a cycle at module import time.
    from .notifications import notification_event

    await manager.publish(recipient_id, notification_event(db, notification))


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


def _feed_seed(seed: str | None, viewer: User) -> int:
    """A stable integer seed for one viewer's feed ordering.

    The client sends a seed so that paging through the feed keeps one order —
    without it, page 2 would be a different shuffle of the same pool and the
    same post could appear twice while another was skipped. When no seed is
    supplied, derive a per-viewer one so a bare request still varies between
    accounts but stays put across a reload.
    """
    material = seed if seed else f"viewer:{viewer.id}"
    digest = hashlib.sha256(material.encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _age_hours(created_at: datetime, now: datetime) -> float:
    """Hours between two timestamps, tolerating SQLite's naive datetimes.

    SQLite has no timezone type, so a value written as UTC-aware comes back
    naive. Subtracting that from an aware `now` raises, so the naive case is
    read as UTC — which is how it was written.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 3600.0)


def _feed_weight(post: Post, like_counts: dict[int, int], comment_counts: dict[int, int], now: datetime) -> float:
    """How strongly a post should be pulled toward the top of the feed.

    Recency dominates — it halves over a day — and engagement only nudges, so a
    month-old post with a lot of likes cannot outrank something from this
    morning. Engagement is damped logarithmically so a single very popular post
    does not monopolise the top of every feed.
    """
    recency = 1.0 / (1.0 + _age_hours(post.created_at, now) / 24.0)
    engagement = like_counts.get(post.id, 0) + 2 * comment_counts.get(post.id, 0)
    return recency * (1.0 + math.log1p(engagement))


def _unit_interval(seed: int, post_id: int) -> float:
    """A deterministic value in (0, 1) for one seed and post.

    Hashed from the seed and id rather than drawn from a shared random stream so
    the value for a post never depends on how many posts came before it — which
    is what keeps a page's order identical to its order within the whole feed.
    """
    digest = hashlib.sha256(f"{seed}:{post_id}".encode()).digest()
    return (int.from_bytes(digest[:8], "big") + 0.5) / 2**64


def _ordered_feed(
    db: DbSession,
    query,
    viewer: User,
    seed: str | None,
    page: int,
    page_size: int,
) -> Page:
    """Order a feed as a random draw weighted by relevance, stable per seed.

    Each post gets a random key scaled by its relevance weight, and the feed is
    the ascending sort of those keys. That is a weighted random permutation: the
    order is genuinely random rather than "newest first", but a fresh or engaging
    post is more likely to land near the top, which is what makes it a relevance
    ordering rather than a coin toss.

    Doing it with a key per post (instead of shuffling a list) means the order is
    a pure function of the seed and the post, so page two of a session continues
    the same ordering instead of reshuffling and repeating or skipping posts.

    The candidate pool is the most recent ``FEED_POOL_SIZE`` rows, so the cost
    does not grow with the size of the site.
    """
    pool = list(db.scalars(query.limit(FEED_POOL_SIZE)).all())
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    if not pool:
        return Page(items=[], total=total, page=page, page_size=page_size, has_more=False)

    ids = [post.id for post in pool]
    like_counts = dict(
        db.execute(
            select(Like.post_id, func.count(Like.id)).where(Like.post_id.in_(ids)).group_by(Like.post_id)
        ).all()
    )
    comment_counts = dict(
        db.execute(
            select(Comment.post_id, func.count(Comment.id))
            .where(Comment.post_id.in_(ids))
            .group_by(Comment.post_id)
        ).all()
    )

    feed_seed = _feed_seed(seed, viewer)
    now = utcnow()

    def draw_key(post: Post) -> float:
        # -log(u) is exponential with rate 1, so dividing by the weight gives
        # rate `weight`: the smallest key is the post most likely to be drawn
        # first, in proportion to its weight.
        return -math.log(_unit_interval(feed_seed, post.id)) / _feed_weight(
            post, like_counts, comment_counts, now
        )

    ordered = sorted(pool, key=draw_key)

    window = ordered[(page - 1) * page_size : page * page_size]
    items = serialize_posts(db, window, viewer)
    return Page(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total and page * page_size < len(ordered),
    )


@router.get("/feed", response_model=Page)
def feed(
    db: DbSession,
    viewer: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    scope: str = Query("following", pattern="^(following|all)$"),
    seed: str | None = Query(None, max_length=64),
) -> Page:
    """The home timeline: posts from followed accounts, or everything when scope=all.

    Ordered by relevance rather than strictly by time, with a per-load shuffle
    so the feed varies between visits. ``seed`` pins the shuffle for the whole
    of one browsing session so "load more" stays consistent.
    """

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

        return _ordered_feed(db, query, viewer, seed, page, page_size).model_dump(mode="json")

    return cache.get_or_set(
        "feed",
        settings.cache_ttl_seconds,
        (viewer.id, page, page_size, scope, seed or ""),
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
    if post.repost_of_id is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A repost has no text to edit")
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
    # Remove shares of this post first. The ON DELETE CASCADE on repost_of_id
    # would cover a database created by this version, but a pre-existing one
    # gets the column from ensure_schema(), which cannot add the constraint —
    # and the test suite builds its tables without the SQLite foreign_keys
    # pragma, so the cascade does not fire there either. Doing it explicitly is
    # both correct everywhere and self-evident.
    db.execute(delete(Post).where(Post.repost_of_id == post.id))
    db.delete(post)
    db.commit()
    invalidate_post_reads()


@router.post("/posts/{post_id}/like", response_model=PostOut)
async def like_post(post_id: int, db: DbSession, user: CurrentUser) -> PostOut:
    post = _get_post_or_404(db, post_id)
    existing = db.scalar(select(Like).where(Like.post_id == post.id, Like.user_id == user.id))
    if existing is None:
        db.add(Like(post_id=post.id, user_id=user.id))
        try:
            # Flush the like on its own so a lost race with a second tap is
            # caught here, before the notification is queued. The unique
            # constraint is what makes the duplicate insert fail; the loser
            # should still report success, because liking twice ends in the
            # same state as liking once.
            db.flush()
        except IntegrityError:
            db.rollback()
        else:
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


def serialize_comments(db: DbSession, comments: list[Comment], viewer: User | None) -> list[CommentOut]:
    """Serialize a page's comments, newest replies attached to their parent.

    All counts and viewer flags are computed with one query each over the whole
    batch, so a thread of any length costs a fixed number of round-trips rather
    than one per comment.
    """
    if not comments:
        return []
    ids = [comment.id for comment in comments]
    authors = serialize_users(db, [comment.author for comment in comments], viewer)

    like_counts = dict(
        db.execute(
            select(CommentLike.comment_id, func.count(CommentLike.id))
            .where(CommentLike.comment_id.in_(ids))
            .group_by(CommentLike.comment_id)
        ).all()
    )
    liked_ids: set[int] = set()
    if viewer is not None:
        liked_ids = {
            row[0]
            for row in db.execute(
                select(CommentLike.comment_id).where(
                    CommentLike.comment_id.in_(ids), CommentLike.user_id == viewer.id
                )
            ).all()
        }

    return [
        CommentOut(
            id=comment.id,
            body=comment.body,
            created_at=comment.created_at,
            author=authors[comment.author_id],
            parent_id=comment.parent_id,
            like_count=like_counts.get(comment.id, 0),
            liked_by_me=comment.id in liked_ids,
            replies=[],
        )
        for comment in comments
    ]


@router.get("/posts/{post_id}/comments", response_model=list[CommentOut])
def list_comments(post_id: int, db: DbSession, viewer: CurrentUser) -> list[CommentOut]:
    """Every comment on a post, top-level first with one level of replies.

    The whole thread is returned in reading order: a comment, then its replies,
    then the next comment. Replies are attached to their parent rather than sent
    as a separate list, so the client does not have to stitch them together.
    """
    _get_post_or_404(db, post_id)
    comments = list(
        db.scalars(
            select(Comment)
            .where(Comment.post_id == post_id)
            .order_by(Comment.created_at.asc(), Comment.id.asc())
        ).all()
    )
    flat = serialize_comments(db, comments, viewer)
    by_id = {item.id: item for item in flat}
    roots: list[CommentOut] = []
    for item in flat:
        parent = by_id.get(item.parent_id) if item.parent_id is not None else None
        # A reply whose parent is not in this post (should not happen) surfaces
        # as a top-level comment instead of being dropped.
        if parent is None:
            roots.append(item)
        else:
            parent.replies.append(item)
    return roots


@router.post("/posts/{post_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
async def create_comment(post_id: int, payload: CommentCreate, db: DbSession, user: CurrentUser) -> CommentOut:
    post = _get_post_or_404(db, post_id)
    parent = None
    if payload.parent_id is not None:
        parent = db.get(Comment, payload.parent_id)
        if parent is None or parent.post_id != post.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")

    comment = Comment(post_id=post.id, author_id=user.id, body=payload.body.strip(), parent_id=parent.id if parent else None)
    db.add(comment)
    # Notify the post author about a top-level comment, and the person being
    # answered about a reply. The wording is chosen per recipient: a reply to
    # someone else's comment is not also "a reply to your post". When both point
    # at the same account, one notification is enough.
    recipients: list[tuple[int, str]] = []
    if post.author_id != user.id:
        recipients.append((post.author_id, "reply" if parent is not None else "comment"))
    if parent is not None and parent.author_id != user.id and parent.author_id != post.author_id:
        recipients.append((parent.author_id, "reply"))
    for recipient_id, kind in recipients:
        await _notify(
            db,
            Notification(
                user_id=recipient_id,
                actor_id=user.id,
                kind=kind,
                post_id=post.id,
            ),
            recipient_id,
        )
    db.commit()
    db.refresh(comment)
    invalidate_post_reads()
    return serialize_comments(db, [comment], user)[0]


@router.post("/comments/{comment_id}/like", response_model=CommentOut)
async def like_comment(comment_id: int, db: DbSession, user: CurrentUser) -> CommentOut:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    existing = db.scalar(
        select(CommentLike).where(
            CommentLike.comment_id == comment.id, CommentLike.user_id == user.id
        )
    )
    if existing is None:
        db.add(CommentLike(comment_id=comment.id, user_id=user.id))
        try:
            # Same race as a post like: the unique constraint rejects the
            # duplicate insert, and that is a success from the caller's view.
            db.flush()
        except IntegrityError:
            db.rollback()
        else:
            if comment.author_id != user.id:
                await _notify(
                    db,
                    Notification(
                        user_id=comment.author_id,
                        actor_id=user.id,
                        kind="comment_like",
                        post_id=comment.post_id,
                    ),
                    comment.author_id,
                )
            db.commit()
            db.refresh(comment)
    return serialize_comments(db, [comment], user)[0]


@router.delete("/comments/{comment_id}/like", response_model=CommentOut)
def unlike_comment(comment_id: int, db: DbSession, user: CurrentUser) -> CommentOut:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    existing = db.scalar(
        select(CommentLike).where(
            CommentLike.comment_id == comment.id, CommentLike.user_id == user.id
        )
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        db.refresh(comment)
    return serialize_comments(db, [comment], user)[0]


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: int, db: DbSession, user: CurrentUser) -> None:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    # The comment author or the post author may remove a comment.
    if comment.author_id != user.id and comment.post.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot delete this comment")
    # Remove the whole subtree. A database migrated from an earlier version has
    # no FK constraint for the cascade to follow, and the ORM cascade does not
    # apply to these bulk deletes, so walk the descendants explicitly.
    doomed = [comment.id]
    frontier = [comment.id]
    while frontier:
        children = list(
            db.scalars(select(Comment.id).where(Comment.parent_id.in_(frontier))).all()
        )
        if not children:
            break
        doomed.extend(children)
        frontier = children
    db.execute(delete(Comment).where(Comment.id.in_(doomed)))
    db.commit()
    invalidate_post_reads()


def _original_of(db: DbSession, post: Post) -> Post:
    """The post a share refers to.

    Reposting a repost shares the underlying original, so nesting never goes
    past one level and a chain of shares cannot be built up.
    """
    if post.repost_of_id is None:
        return post
    return db.get(Post, post.repost_of_id) or post


@router.post("/posts/{post_id}/repost", response_model=PostOut, status_code=status.HTTP_201_CREATED)
async def repost(post_id: int, db: DbSession, user: CurrentUser) -> PostOut:
    """Share a post to your own followers.

    Returns the *original* post, like the like/unlike endpoints do, so the
    caller can update its counts and repost state in place. The new share
    appears in followers' feeds as a separate row.
    """
    post = _original_of(db, _get_post_or_404(db, post_id))
    if post.author_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot repost your own post")

    existing = db.scalar(
        select(Post).where(Post.author_id == user.id, Post.repost_of_id == post.id)
    )
    if existing is None:
        db.add(Post(author_id=user.id, body="", repost_of_id=post.id))
        await _notify(
            db,
            Notification(user_id=post.author_id, actor_id=user.id, kind="repost", post_id=post.id),
            post.author_id,
        )
        db.commit()
        db.refresh(post)
        invalidate_post_reads()
    return serialize_posts(db, [post], user)[0]


@router.delete("/posts/{post_id}/repost", response_model=PostOut)
def unrepost(post_id: int, db: DbSession, user: CurrentUser) -> PostOut:
    """Undo a share. Idempotent, like unlike."""
    post = _original_of(db, _get_post_or_404(db, post_id))
    existing = db.scalar(
        select(Post).where(Post.author_id == user.id, Post.repost_of_id == post.id)
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        db.refresh(post)
        invalidate_post_reads()
    return serialize_posts(db, [post], user)[0]


@router.get("/posts/{post_id}/reposts", response_model=list[UserPublic])
def list_reposts(post_id: int, db: DbSession, viewer: CurrentUser) -> list[UserPublic]:
    """Accounts that have shared this post, most recent first."""

    def produce() -> list[dict]:
        post = _original_of(db, _get_post_or_404(db, post_id))
        sharers = list(
            db.scalars(
                select(User)
                .join(Post, Post.author_id == User.id)
                .where(Post.repost_of_id == post.id)
                .order_by(Post.created_at.desc())
            ).all()
        )
        serialized = serialize_users(db, sharers, viewer)
        return [serialized[u.id].model_dump(mode="json") for u in sharers]

    return cache.get_or_set("posts", settings.cache_ttl_seconds, (viewer.id, "reposts", post_id), produce)


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
