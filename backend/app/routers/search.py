"""Search across posts and people."""

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from ..deps import DbSession, OptionalUser, serialize_posts, serialize_users
from ..models import Post, User
from ..schemas import PostOut, UserPublic

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/posts", response_model=list[PostOut])
def search_posts(
    db: DbSession,
    viewer: OptionalUser,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
) -> list[PostOut]:
    term = f"%{q.strip().lower()}%"
    query = (
        select(Post)
        .where(or_(Post.body.ilike(term), Post.author_id.in_(
            select(User.id).where(or_(User.username.ilike(term), User.display_name.ilike(term)))
        )))
        .order_by(Post.created_at.desc())
        .limit(limit)
    )
    return serialize_posts(db, list(db.scalars(query).all()), viewer)


@router.get("/users", response_model=list[UserPublic])
def search_users(
    db: DbSession,
    viewer: OptionalUser,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
) -> list[UserPublic]:
    term = f"%{q.strip().lower()}%"
    query = (
        select(User)
        .where(or_(User.username.ilike(term), User.display_name.ilike(term), User.bio.ilike(term)))
        .order_by(User.username.asc())
        .limit(limit)
    )
    users = list(db.scalars(query).all())
    serialized = serialize_users(db, users, viewer)
    return [serialized[u.id] for u in users]


@router.get("")
def search_all(
    db: DbSession,
    viewer: OptionalUser,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    return {
        "query": q,
        "users": search_users(db, viewer, q=q, limit=limit),
        "posts": search_posts(db, viewer, q=q, limit=limit),
    }
