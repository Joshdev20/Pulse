"""Pydantic request/response schemas."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer


def _as_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes.

    SQLite has no timezone type, so a value written as UTC-aware comes back
    naive. Serializing that without an offset makes the browser parse it as
    local time, which is how a just-created post reads as "1h ago". Forcing an
    explicit UTC offset keeps the wire format unambiguous.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Every timestamp the API emits goes through this, so clients can always rely on
# the offset being present.
UTCDatetime = Annotated[datetime, PlainSerializer(_as_utc, return_type=datetime)]


class UserBase(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=64)
    bio: str = Field(default="", max_length=280)
    location: str = Field(default="", max_length=64)
    website: str = Field(default="", max_length=255)


class UserCreate(UserBase):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    bio: str | None = Field(default=None, max_length=280)
    location: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=255)
    avatar_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    bio: str
    location: str
    website: str
    avatar_color: str
    created_at: UTCDatetime
    follower_count: int = 0
    following_count: int = 0
    post_count: int = 0
    is_following: bool = False
    # Lets the client show the moderation entry point only to moderators.
    is_admin: bool = False


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    created_at: UTCDatetime
    author: UserPublic


class PostCreate(BaseModel):
    body: str = Field(default="", max_length=2000)
    media_ids: list[int] = Field(default_factory=list, max_length=4)


class PostUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    content_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    alt_text: str = ""
    url: str


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    created_at: UTCDatetime
    edited_at: UTCDatetime | None
    author: UserPublic
    tags: list[TagOut] = []
    media: list[MediaOut] = []
    like_count: int = 0
    comment_count: int = 0
    liked_by_me: bool = False


class MessageCreate(BaseModel):
    body: str = Field(default="", max_length=4000)
    media_ids: list[int] = Field(default_factory=list, max_length=4)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    body: str
    created_at: UTCDatetime
    sender: UserPublic
    media: list[MediaOut] = []
    mine: bool = False


class ConversationOut(BaseModel):
    id: int
    created_at: UTCDatetime
    last_message_at: UTCDatetime
    partner: UserPublic
    last_message: MessageOut | None = None
    unread_count: int = 0


class ConversationStart(BaseModel):
    username: str = Field(min_length=1, max_length=32)


class TypingEvent(BaseModel):
    conversation_id: int
    typing: bool = True


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    read: bool
    created_at: UTCDatetime
    post_id: int | None
    actor: UserPublic | None


class Page(BaseModel):
    items: list[PostOut]
    total: int
    page: int
    page_size: int
    has_more: bool


class SuggestedUser(BaseModel):
    user: UserPublic
    reason: str


class Stats(BaseModel):
    users: int
    posts: int
    likes: int
    comments: int


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------


class AdminUser(BaseModel):
    """A user as seen by a moderator, including moderation state."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    display_name: str
    bio: str
    location: str
    website: str
    avatar_color: str
    created_at: UTCDatetime
    is_active: bool
    is_admin: bool
    is_banned: bool
    banned_reason: str = ""
    follower_count: int = 0
    following_count: int = 0
    post_count: int = 0
    comment_count: int = 0


class AdminUserPage(BaseModel):
    items: list[AdminUser]
    total: int
    page: int
    page_size: int
    has_more: bool


class AdminPost(BaseModel):
    """A post in the moderation queue, with enough context to judge it."""

    id: int
    body: str
    created_at: UTCDatetime
    author_id: int
    author_username: str
    author_display_name: str
    like_count: int = 0
    comment_count: int = 0
    media_count: int = 0


class AdminPostPage(BaseModel):
    items: list[AdminPost]
    total: int
    page: int
    page_size: int
    has_more: bool


class AdminCount(BaseModel):
    label: str
    value: int


class AdminStats(BaseModel):
    """Analytics for the admin dashboard."""

    total_users: int
    active_users: int
    banned_users: int
    admin_users: int
    new_users_7d: int
    new_users_30d: int
    total_posts: int
    posts_7d: int
    posts_30d: int
    total_comments: int
    total_likes: int
    total_messages: int
    total_conversations: int
    total_media: int
    engagement_rate: float
    avg_posts_per_user: float
    top_posters: list[AdminCount]
    top_tags: list[AdminCount]


class UserModeration(BaseModel):
    """Ban a user, optionally recording why."""

    banned: bool = True
    reason: str = Field(default="", max_length=280)


class AdminFlagUpdate(BaseModel):
    is_admin: bool


class AdminComment(BaseModel):
    """A comment in the moderation queue."""

    id: int
    body: str
    created_at: UTCDatetime
    post_id: int
    author_id: int
    author_username: str
    author_display_name: str


class AdminCommentPage(BaseModel):
    items: list[AdminComment]
    total: int
    page: int
    page_size: int
    has_more: bool
