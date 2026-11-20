"""Pydantic request/response schemas."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator


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
    # Set to an uploaded image id to use a picture, or null to go back to the
    # generated initials avatar.
    avatar_media_id: int | None = None
    # Same, for the profile header image.
    cover_media_id: int | None = None


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    bio: str
    location: str
    website: str
    avatar_color: str
    # Resolved media URL for an uploaded picture, or null for the initials
    # avatar. A URL rather than an id so the client needs no second lookup.
    avatar_url: str | None = None
    # Resolved URL for the profile header image, or null for the plain gradient.
    cover_url: str | None = None
    created_at: UTCDatetime
    follower_count: int = 0
    following_count: int = 0
    post_count: int = 0
    is_following: bool = False
    # Lets the client show the moderation entry point only to moderators.
    is_admin: bool = False
    is_verified: bool = False
    # For the "last seen" line in a direct-message thread. Null when the account
    # has never been active since tracking was added. Always emitted for the
    # partner; the thread header decides whether to show it.
    last_seen_at: UTCDatetime | None = None
    # Whether the account holds an open realtime socket right now. Computed per
    # response from the connection manager, so it is "online now", not "online
    # at the last request".
    is_online: bool = False


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=8, max_length=128)


class PasswordResetIssued(BaseModel):
    """Response to a reset request.

    ``reset_token`` is only populated when no mail transport is configured, so a
    deployment without SMTP can still be used and tested. It is null in any
    environment that can actually send the link.
    """

    detail: str = "If that email is registered, a reset link has been sent."
    reset_token: str | None = None
    reset_url: str | None = None


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
    # Present when the comment answers another comment in the same thread.
    parent_id: int | None = None


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    created_at: UTCDatetime
    author: UserPublic
    parent_id: int | None = None
    like_count: int = 0
    liked_by_me: bool = False
    # Answers to this comment, nested to whatever depth the thread reaches. The
    # client decides how far to indent.
    replies: list["CommentOut"] = []


class PostCreate(BaseModel):
    body: str = Field(default="", max_length=2000)
    media_ids: list[int] = Field(default_factory=list, max_length=4)


class PostUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """`min_length` counts spaces, so "   " passed and then stripped to nothing.

        Both edit paths write `body.strip()`, so a whitespace-only body used to
        blank a post while reporting success.
        """
        if not value.strip():
            raise ValueError("A post needs some text")
        return value


class PostTranslation(BaseModel):
    """The result of asking to translate a post.

    ``translated`` is null when the post is already in the UI's language, is too
    short to detect, or the translation service could not be reached — all of
    which mean the same thing to the client: no toggle to show.
    """

    source: str | None = None
    source_name: str | None = None
    translated: str | None = None


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
    # A small image of the video's first real frame, so a tile can render
    # without downloading the video. Null for images and for videos uploaded
    # before posters were captured.
    poster_url: str | None = None
    # Length in milliseconds, so the duration badge needs no video fetch.
    duration_ms: int | None = None


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
    repost_count: int = 0
    reposted_by_me: bool = False
    # Set when this row is a repost: `author`/`created_at` describe who shared
    # it and when, while this holds the post that was shared. Null for an
    # original. Always one level deep, so the recursion terminates.
    repost_of: "PostOut | None" = None
    # The detected language of `body`, and its display name. Null when the post
    # is too short, the guess too weak, or detection is unavailable. The client
    # shows a translate prompt only when this is present and not the UI language.
    language: str | None = None
    language_name: str | None = None
    # How many distinct accounts have seen this post. Shown under the post.
    view_count: int = 0


# The annotation above is a forward reference, which Pydantic resolves lazily.
# Without this it stays unresolved and the first repost fails to serialize.
PostOut.model_rebuild()


class MessageCreate(BaseModel):
    body: str = Field(default="", max_length=4000)
    media_ids: list[int] = Field(default_factory=list, max_length=4)
    # Set by a swipe-to-reply. Must reference another message in the same
    # conversation; the route rejects anything else rather than trusting it.
    reply_to_id: int | None = None
    # A sticker id from the shipped catalogue. Validated against it by the
    # route, because a client-supplied id is untrusted input.
    sticker: str | None = Field(default=None, max_length=64)
    # Set when this message forwards another one. The route checks the caller
    # could already read the source, so the id cannot be used to reach into a
    # thread the sender is not part of.
    forwarded_from_id: int | None = None


class MessageEdit(BaseModel):
    """A replacement body for a message the caller wrote."""

    body: str = Field(min_length=1, max_length=4000)


class MessageQuote(BaseModel):
    """A compact view of the message a reply points at.

    Deliberately not a full ``MessageOut``: this is rendered as one line above
    the reply, so it carries only what that line shows and cannot recurse.
    """

    id: int
    body: str
    sender_username: str
    sender_display_name: str
    has_media: bool = False


class CallLog(BaseModel):
    """How a call in the thread is described to a reader.

    The state is resolved per reader, because the same call is a different
    event to each side: an unanswered outgoing call is "no answer" to the
    caller and "missed" to the person who did not pick up. `by_me` is what lets
    the client word it, so the wording lives in one place rather than in the
    stored row.
    """

    mode: Literal["audio", "video"]
    # `ringing` and `active` are live states; `answered`, `missed`, `declined`
    # and `cancelled` are final. A live state carries no duration, so the client
    # shows a timer rather than a fixed one.
    state: Literal["ringing", "active", "answered", "missed", "declined", "cancelled"]
    # Whether the reader placed this call. Decides "Outgoing" vs "Incoming".
    by_me: bool = False
    seconds: int | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    body: str
    created_at: UTCDatetime
    sender: UserPublic
    media: list[MediaOut] = []
    mine: bool = False
    # A sticker id from the shipped catalogue, when this message is one.
    sticker: str | None = None
    # Set when this row is a call rather than a message. Such a row has no
    # body, is not editable or forwardable, and renders as a centred entry.
    call: CallLog | None = None
    # The quoted message, when this is a reply and the target still exists.
    reply_to: MessageQuote | None = None
    like_count: int = 0
    liked_by_me: bool = False
    # When the sender last changed the body, so the thread can mark it edited.
    edited_at: UTCDatetime | None = None
    # Whether this message was forwarded rather than written in this thread.
    forwarded: bool = False
    # The reader's partner has read past this message. Only ever true on the
    # reader's own messages; the other side's messages are read by definition.
    read_by_partner: bool = False


class ConversationOut(BaseModel):
    id: int
    created_at: UTCDatetime
    last_message_at: UTCDatetime
    partner: UserPublic
    last_message: MessageOut | None = None
    unread_count: int = 0
    # How far the *partner* has read in this thread, so the thread can mark the
    # reader's own messages as seen without a second request.
    partner_read_at: UTCDatetime | None = None


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
    avatar_url: str | None = None
    cover_url: str | None = None
    created_at: UTCDatetime
    is_active: bool
    is_admin: bool
    is_banned: bool
    banned_reason: str = ""
    is_verified: bool = False
    verified_at: UTCDatetime | None = None
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


class AdminVerification(BaseModel):
    """Grant or revoke a verified badge."""

    is_verified: bool = True


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


class AnnouncementBroadcast(BaseModel):
    """An official post to push to every account."""

    post_id: int


class AnnouncementBroadcastResult(BaseModel):
    """What the broadcast did, so a moderator can see it reached people."""

    post_id: int
    notified: int
    pushed: int


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
