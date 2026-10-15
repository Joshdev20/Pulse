"""Registration, login, password reset and current-user endpoints."""

from datetime import timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from .. import mail
from ..config import settings
from ..deps import CurrentUser, DbSession, serialize_user
from ..models import PasswordReset, User, utcnow
from ..schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    PasswordResetIssued,
    ResetPasswordRequest,
    Token,
    UserCreate,
    UserPublic,
)
from ..security import (
    create_access_token,
    hash_password,
    hash_reset_token,
    new_reset_token,
    verify_password,
)

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


def _invalidate_resets(db: DbSession, user_id: int) -> None:
    """Consume every outstanding reset for a user.

    Issuing a new link or completing a reset retires the others, so an older
    link that is still in an inbox cannot be used afterwards.
    """
    now = utcnow()
    for row in db.scalars(
        select(PasswordReset).where(
            PasswordReset.user_id == user_id, PasswordReset.used_at.is_(None)
        )
    ).all():
        row.used_at = now


@router.post("/forgot-password", response_model=PasswordResetIssued)
def forgot_password(payload: ForgotPasswordRequest, db: DbSession) -> PasswordResetIssued:
    """Start a password reset.

    The response is identical whether or not the address is registered, so the
    endpoint cannot be used to enumerate accounts.
    """
    email = payload.email.lower().strip()
    user = db.scalar(select(User).where(func.lower(User.email) == email))

    if user is None or not user.is_active:
        return PasswordResetIssued()

    _invalidate_resets(db, user.id)
    token, token_hash = new_reset_token()
    db.add(
        PasswordReset(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=utcnow() + timedelta(minutes=settings.password_reset_ttl_minutes),
        )
    )
    db.commit()

    reset_url = f"{settings.frontend_base_url.rstrip('/')}/reset-password?token={token}"
    mail.send_password_reset(user.email, reset_url, settings.password_reset_ttl_minutes)

    # Only ever populated when no mail transport exists, so a deployment without
    # SMTP can still complete the flow. See Settings.may_return_reset_token.
    if settings.may_return_reset_token:
        return PasswordResetIssued(reset_token=token, reset_url=reset_url)
    return PasswordResetIssued()


@router.post("/reset-password", response_model=Token)
def reset_password(payload: ResetPasswordRequest, db: DbSession) -> Token:
    """Complete a reset and sign the user straight in."""
    record = db.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == hash_reset_token(payload.token))
    )
    if record is None or record.used_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is no longer valid")

    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link has expired")

    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is no longer valid")

    user.password_hash = hash_password(payload.password)
    _invalidate_resets(db, user.id)
    db.commit()
    db.refresh(user)
    return Token(access_token=create_access_token(str(user.id)))
