"""Administrator endpoints: analytics, user controls and content moderation.

Every route here depends on ``CurrentAdmin``, which itself depends on
``CurrentUser``. A non-admin, a banned account and an anonymous caller are all
rejected before the handler runs.
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from .. import cache, storage
from ..deps import CurrentAdmin, DbSession, avatar_url_for, cover_url_for
from ..models import (
    Comment,
    Conversation,
    Follow,
    Like,
    Media,
    Message,
    Post,
    Tag,
    User,
    utcnow,
)
from ..realtime import manager
from ..schemas import (
    AdminComment,
    AdminCommentPage,
    AdminCount,
    AdminFlagUpdate,
    AdminPost,
    AdminPostPage,
    AdminStats,
    AdminUser,
    AdminUserPage,
    AdminVerification,
    UserModeration,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Moderation changes who can see and do what, so every cached read that embeds
# author state or counts is dropped.
ADMIN_WRITE_NAMESPACES = ("users", "suggested", "feed", "posts", "stats", "search", "notifications")


def _invalidate() -> None:
    cache.bump(*ADMIN_WRITE_NAMESPACES)


def _get_user_or_404(db: DbSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


def _serialize_admin_users(db: DbSession, users: list[User]) -> list[AdminUser]:
    """Serialize moderators' view of users with batched aggregate counts."""
    if not users:
        return []
    ids = [user.id for user in users]

    post_counts = dict(
        db.execute(
            select(Post.author_id, func.count(Post.id))
            .where(Post.author_id.in_(ids))
            .group_by(Post.author_id)
        ).all()
    )
    comment_counts = dict(
        db.execute(
            select(Comment.author_id, func.count(Comment.id))
            .where(Comment.author_id.in_(ids))
            .group_by(Comment.author_id)
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

    return [
        AdminUser(
            id=user.id,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            bio=user.bio,
            location=user.location,
            website=user.website,
            avatar_color=user.avatar_color,
            avatar_url=avatar_url_for(user),
            cover_url=cover_url_for(user),
            created_at=user.created_at,
            is_active=user.is_active,
            is_admin=user.is_admin,
            is_banned=user.is_banned,
            banned_reason=user.banned_reason,
            is_verified=user.is_verified,
            verified_at=user.verified_at,
            post_count=post_counts.get(user.id, 0),
            comment_count=comment_counts.get(user.id, 0),
            follower_count=follower_counts.get(user.id, 0),
            following_count=following_counts.get(user.id, 0),
        )
        for user in users
    ]


@router.get("/stats", response_model=AdminStats)
def admin_stats(db: DbSession, _admin: CurrentAdmin) -> AdminStats:
    """Platform analytics for the admin dashboard.

    Counted with aggregate queries rather than by loading rows, so this stays
    cheap as the tables grow.
    """

    def count(model, *conditions) -> int:
        query = select(func.count()).select_from(model)
        for condition in conditions:
            query = query.where(condition)
        return db.scalar(query) or 0

    now = utcnow()
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    total_users = count(User)
    total_posts = count(Post)
    total_likes = count(Like)
    total_comments = count(Comment)

    top_posters = [
        AdminCount(label=username, value=posts)
        for username, posts in db.execute(
            select(User.username, func.count(Post.id))
            .join(Post, Post.author_id == User.id)
            .group_by(User.username)
            .order_by(func.count(Post.id).desc(), User.username)
            .limit(5)
        ).all()
    ]
    top_tags = [
        AdminCount(label=name, value=uses)
        for name, uses in db.execute(
            select(Tag.name, func.count(Post.id))
            .join(Tag.posts)
            .group_by(Tag.name)
            .order_by(func.count(Post.id).desc(), Tag.name)
            .limit(5)
        ).all()
    ]

    # Interactions per post: the usual single-number health signal.
    engagement = round((total_likes + total_comments) / total_posts, 2) if total_posts else 0.0

    return AdminStats(
        total_users=total_users,
        active_users=count(User, User.is_active.is_(True)),
        banned_users=count(User, User.is_banned.is_(True)),
        admin_users=count(User, User.is_admin.is_(True)),
        new_users_7d=count(User, User.created_at >= since_7d),
        new_users_30d=count(User, User.created_at >= since_30d),
        total_posts=total_posts,
        posts_7d=count(Post, Post.created_at >= since_7d),
        posts_30d=count(Post, Post.created_at >= since_30d),
        total_comments=total_comments,
        total_likes=total_likes,
        total_messages=count(Message),
        total_conversations=count(Conversation),
        total_media=count(Media),
        engagement_rate=engagement,
        avg_posts_per_user=round(total_posts / total_users, 2) if total_users else 0.0,
        top_posters=top_posters,
        top_tags=top_tags,
    )


@router.get("/users", response_model=AdminUserPage)
def list_users(
    db: DbSession,
    _admin: CurrentAdmin,
    q: str | None = Query(None, max_length=64, description="Match username, display name or email"),
    status_filter: str | None = Query(
        None,
        alias="status",
        pattern="^(active|banned|admin|all)$",
        description="Restrict to a moderation state",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> AdminUserPage:
    """Paginated, searchable user list for the moderation table."""
    query = select(User)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(User.username).like(needle),
                func.lower(User.display_name).like(needle),
                func.lower(User.email).like(needle),
            )
        )
    if status_filter == "active":
        query = query.where(User.is_banned.is_(False))
    elif status_filter == "banned":
        query = query.where(User.is_banned.is_(True))
    elif status_filter == "admin":
        query = query.where(User.is_admin.is_(True))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    users = list(
        db.scalars(
            query.order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return AdminUserPage(
        items=_serialize_admin_users(db, users),
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.get("/users/{user_id}", response_model=AdminUser)
def get_user(user_id: int, db: DbSession, _admin: CurrentAdmin) -> AdminUser:
    return _serialize_admin_users(db, [_get_user_or_404(db, user_id)])[0]


def _ensure_admin_remains(db: DbSession, target: User) -> None:
    """Refuse an action that would leave the platform with no administrator.

    Demoting or deleting the only admin locks everyone out of moderation, with
    no route back in short of direct database access.
    """
    if not target.is_admin:
        return
    remaining = db.scalar(
        select(func.count()).select_from(User).where(User.is_admin.is_(True), User.id != target.id)
    )
    if not remaining:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This is the only administrator, so the action is not allowed",
        )


@router.post("/users/{user_id}/ban", response_model=AdminUser)
async def set_ban(
    user_id: int, payload: UserModeration, db: DbSession, admin: CurrentAdmin
) -> AdminUser:
    """Ban or unban a user. A ban takes effect immediately, existing token and all."""
    target = _get_user_or_404(db, user_id)
    if payload.banned:
        # A moderator banning themselves is almost always a misclick, and it
        # would end their own session.
        if target.id == admin.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "You cannot ban your own account",
            )
        _ensure_admin_remains(db, target)

    target.is_banned = payload.banned
    target.banned_reason = payload.reason.strip() if payload.banned else ""
    db.commit()
    db.refresh(target)
    _invalidate()

    if payload.banned:
        # Tell any open socket to drop the session rather than waiting for the
        # user's next request to fail.
        await manager.publish(target.id, {"type": "banned", "reason": target.banned_reason})

    return _serialize_admin_users(db, [target])[0]


@router.post("/users/{user_id}/admin", response_model=AdminUser)
def set_admin(
    user_id: int, payload: AdminFlagUpdate, db: DbSession, admin: CurrentAdmin
) -> AdminUser:
    """Grant or revoke moderator rights."""
    target = _get_user_or_404(db, user_id)
    if not payload.is_admin:
        # Stepping down is allowed as long as another admin remains.
        _ensure_admin_remains(db, target)

    target.is_admin = payload.is_admin
    db.commit()
    db.refresh(target)
    _invalidate()
    return _serialize_admin_users(db, [target])[0]


@router.post("/users/{user_id}/verify", response_model=AdminUser)
def set_verification(
    user_id: int, payload: AdminVerification, db: DbSession, _admin: CurrentAdmin
) -> AdminUser:
    """Grant or revoke a verified badge.

    Unlike banning, this carries no self-lockout risk, so there is no guard: a
    moderator may verify themselves.
    """
    target = _get_user_or_404(db, user_id)
    target.is_verified = payload.is_verified
    target.verified_at = utcnow() if payload.is_verified else None
    db.commit()
    db.refresh(target)
    _invalidate()
    return _serialize_admin_users(db, [target])[0]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: DbSession, admin: CurrentAdmin) -> None:
    """Delete a user and, by cascade, their posts, comments, likes and messages.

    Uploaded files are removed from disk first: the database rows cascade, but
    the files on the volume would otherwise be orphaned.
    """
    target = _get_user_or_404(db, user_id)
    if target.id == admin.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You cannot delete your own account from here",
        )
    _ensure_admin_remains(db, target)

    stored_names = [
        row[0]
        for row in db.execute(
            select(Media.stored_name).where(Media.owner_id == target.id)
        ).all()
    ]
    username = target.username
    db.delete(target)
    db.commit()
    _invalidate()

    for name in stored_names:
        storage.delete(name)

    await manager.publish(target.id, {"type": "account_deleted", "username": username})


@router.get("/posts", response_model=AdminPostPage)
def list_posts(
    db: DbSession,
    _admin: CurrentAdmin,
    q: str | None = Query(None, max_length=200, description="Search post bodies"),
    author: str | None = Query(None, max_length=32, description="Filter by author username"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> AdminPostPage:
    """The moderation queue: recent posts, optionally filtered."""
    query = select(Post).join(User, User.id == Post.author_id)
    if q and q.strip():
        query = query.where(Post.body.ilike(f"%{q.strip()}%"))
    if author and author.strip():
        query = query.where(func.lower(User.username) == author.strip().lower())

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    posts = list(
        db.scalars(
            query.order_by(Post.created_at.desc(), Post.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    ids = [post.id for post in posts]
    like_counts: dict[int, int] = {}
    comment_counts: dict[int, int] = {}
    media_counts: dict[int, int] = {}
    if ids:
        like_counts = dict(
            db.execute(
                select(Like.post_id, func.count(Like.id))
                .where(Like.post_id.in_(ids))
                .group_by(Like.post_id)
            ).all()
        )
        comment_counts = dict(
            db.execute(
                select(Comment.post_id, func.count(Comment.id))
                .where(Comment.post_id.in_(ids))
                .group_by(Comment.post_id)
            ).all()
        )
        media_counts = dict(
            db.execute(
                select(Media.post_id, func.count(Media.id))
                .where(Media.post_id.in_(ids))
                .group_by(Media.post_id)
            ).all()
        )

    return AdminPostPage(
        items=[
            AdminPost(
                id=post.id,
                body=post.body,
                created_at=post.created_at,
                author_id=post.author_id,
                author_username=post.author.username,
                author_display_name=post.author.display_name,
                like_count=like_counts.get(post.id, 0),
                comment_count=comment_counts.get(post.id, 0),
                media_count=media_counts.get(post.id, 0),
            )
            for post in posts
        ],
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: int, db: DbSession, _admin: CurrentAdmin) -> None:
    """Remove a post regardless of who wrote it, along with its uploaded media."""
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")

    stored_names = [
        row[0] for row in db.execute(select(Media.stored_name).where(Media.post_id == post.id)).all()
    ]
    db.delete(post)
    db.commit()
    _invalidate()
    for name in stored_names:
        storage.delete(name)


@router.get("/comments", response_model=AdminCommentPage)
def list_comments(
    db: DbSession,
    _admin: CurrentAdmin,
    q: str | None = Query(None, max_length=200, description="Search comment bodies"),
    author: str | None = Query(None, max_length=32, description="Filter by author username"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> AdminCommentPage:
    """Comment moderation queue."""
    query = select(Comment).join(User, User.id == Comment.author_id)
    if q and q.strip():
        query = query.where(Comment.body.ilike(f"%{q.strip()}%"))
    if author and author.strip():
        query = query.where(func.lower(User.username) == author.strip().lower())

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    comments = list(
        db.scalars(
            query.order_by(Comment.created_at.desc(), Comment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return AdminCommentPage(
        items=[
            AdminComment(
                id=comment.id,
                body=comment.body,
                created_at=comment.created_at,
                post_id=comment.post_id,
                author_id=comment.author_id,
                author_username=comment.author.username,
                author_display_name=comment.author.display_name,
            )
            for comment in comments
        ],
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: int, db: DbSession, _admin: CurrentAdmin) -> None:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    db.delete(comment)
    db.commit()
    _invalidate()
