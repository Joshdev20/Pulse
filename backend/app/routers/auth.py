"""Registration, login and current-user endpoints."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ..deps import CurrentUser, DbSession, serialize_user
from ..models import User
from ..schemas import LoginRequest, Token, UserCreate, UserPublic
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

AVATAR_COLORS = [
    "#6366f1", "#ec4899", "#f59e0b", "#10b981",
    "#3b82f6", "#8b5cf6", "#ef4444", "#14b8a6",
]


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession) -> Token:
    username = payload.username.strip()
    email = payload.email.lower().strip()

    clash = db.scalar(
        select(User).where(
            (func.lower(User.username) == username.lower()) | (User.email == email)
        )
    )
    if clash is not None:
        field = "username" if clash.username.lower() == username.lower() else "email"
        raise HTTPException(status.HTTP_409_CONFLICT, f"That {field} is already taken")

    user = User(
        username=username,
        email=email,
        display_name=payload.display_name.strip(),
        bio=payload.bio.strip(),
        location=payload.location.strip(),
        website=payload.website.strip(),
        avatar_color=AVATAR_COLORS[hash(email) % len(AVATAR_COLORS)],
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return Token(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: DbSession) -> Token:
    username = payload.username.strip().lower()
    password = payload.password
    if not username or not password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Username and password are required")

    user = db.scalar(
        select(User).where(
            (func.lower(User.username) == username) | (User.email == username)
        )
    )
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been suspended")

    return Token(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserPublic)
def me(db: DbSession, user: CurrentUser) -> UserPublic:
    return serialize_user(db, user, user)
