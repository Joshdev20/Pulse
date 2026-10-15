"""Search across posts and people."""

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from .. import cache
from ..config import settings
from ..deps import CurrentUser, DbSession, serialize_posts, serialize_users
from ..models import Post, User
from ..schemas import PostOut, UserPublic

router = APIRouter(prefix="/api/search", tags=["search"])

# Search results are derived from posts and profiles, so they are cached briefly
# and refreshed whenever a post or follow changes.
SEARCH_TTL = settings.cache_ttl_seconds


@router.get("/posts", response_model=list[PostOut])
def search_posts(
    db: DbSession,
    viewer: CurrentUser,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
) -> list[PostOut]:
    def produce() -> list[dict]:
        term = f"%{q.strip().lower()}%"
        query = (
            select(Post)
            .where(or_(Post.body.ilike(term), Post.author_id.in_(
                select(User.id).where(or_(User.username.ilike(term), User.display_name.ilike(term)))
            )))
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
        posts = list(db.scalars(query).all())
        return [post.model_dump(mode="json") for post in serialize_posts(db, posts, viewer)]

    return cache.get_or_set("search", SEARCH_TTL, (viewer.id, "posts", q.strip().lower(), limit), produce)


@router.get("/users", response_model=list[UserPublic])
def search_users(
    db: DbSession,
    viewer: CurrentUser,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
) -> list[UserPublic]:
    def produce() -> list[dict]:
        term = f"%{q.strip().lower()}%"
        query = (
            select(User)
            .where(or_(User.username.ilike(term), User.display_name.ilike(term), User.bio.ilike(term)))
            .order_by(User.username.asc())
            .limit(limit)
        )
        users = list(db.scalars(query).all())
        serialized = serialize_users(db, users, viewer)
        return [serialized[u.id].model_dump(mode="json") for u in users]

    return cache.get_or_set("search", SEARCH_TTL, (viewer.id, "users", q.strip().lower(), limit), produce)


@router.get("")
def search_all(
    db: DbSession,
    viewer: CurrentUser,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    return {
        "query": q,
        "users": search_users(db, viewer, q=q, limit=limit),
        "posts": search_posts(db, viewer, q=q, limit=limit),
    }
