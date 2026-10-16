"""SQLAlchemy ORM models for the Pulse social platform."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


post_tags = Table(
    "post_tags",
    Base.metadata,
    Column("post_id", ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    bio: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(64), default="")
    website: Mapped[str] = mapped_column(String(255), default="")
    avatar_color: Mapped[str] = mapped_column(String(16), default="#6366f1")
    # An uploaded picture, when the user has one. Falls back to the generated
    # initials-on-colour avatar, so this stays nullable. Deliberately not a
    # foreign key: users <-> media would be a circular reference, and the id is
    # validated against the caller's own uploads in the avatar endpoints.
    avatar_media_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # The profile header image, same rules as avatar_media_id. Both are checked
    # when deciding whether an upload is publicly readable, since either one is
    # shown to anyone who can view the profile.
    cover_media_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Moderators can reach the admin endpoints; a banned account keeps its data
    # but cannot sign in or use an existing token.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    banned_reason: Mapped[str] = mapped_column(String(280), default="")
    # A staff-granted badge that distinguishes official accounts in the UI.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    posts: Mapped[list["Post"]] = relationship(
        back_populates="author", cascade="all, delete-orphan", order_by="Post.created_at.desc()"
    )
    comments: Mapped[list["Comment"]] = relationship(back_populates="author", cascade="all, delete-orphan")
    likes: Mapped[list["Like"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    followers: Mapped[list["Follow"]] = relationship(
        foreign_keys="Follow.following_id",
        back_populates="following",
        cascade="all, delete-orphan",
    )
    following: Mapped[list["Follow"]] = relationship(
        foreign_keys="Follow.follower_id",
        back_populates="follower",
        cascade="all, delete-orphan",
    )

    @property
    def follower_count(self) -> int:
        return len(self.followers)

    @property
    def following_count(self) -> int:
        return len(self.following)


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("follower_id", "following_id", name="uq_follow_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    following_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    follower: Mapped[User] = relationship(foreign_keys=[follower_id], back_populates="following")
    following: Mapped[User] = relationship(foreign_keys=[following_id], back_populates="followers")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    posts: Mapped[list["Post"]] = relationship(secondary=post_tags, back_populates="tags")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A repost is a row pointing at the post it shares, so it flows through the
    # existing feed, pagination and serialization untouched. A repost row has an
    # empty body, no media and no tags: everything shown comes from `repost_of`.
    # Nesting stops at one level — a repost always points at an original.
    repost_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=True, index=True
    )

    author: Mapped[User] = relationship(back_populates="posts")
    comments: Mapped[list["Comment"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    likes: Mapped[list["Like"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    tags: Mapped[list[Tag]] = relationship(secondary=post_tags, back_populates="posts", lazy="selectin")
    media: Mapped[list["Media"]] = relationship(
        primaryjoin="Media.post_id == Post.id",
        foreign_keys="Media.post_id",
        cascade="all, delete-orphan",
        order_by="Media.id",
        lazy="selectin",
    )
    repost_of: Mapped["Post | None"] = relationship(
        remote_side=[id],
        foreign_keys=[repost_of_id],
        lazy="selectin",
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Set when this comment answers another one; null for a top-level comment.
    # Deleting a parent removes its replies rather than orphaning them, so the
    # cascade is declared on both the relationship and the key.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    post: Mapped[Post] = relationship(back_populates="comments")
    author: Mapped[User] = relationship(back_populates="comments")
    parent: Mapped["Comment | None"] = relationship(
        remote_side=[id], back_populates="replies"
    )
    replies: Mapped[list["Comment"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class CommentLike(Base):
    """A like on a comment, separate from a like on the post it belongs to."""

    __tablename__ = "comment_likes"
    __table_args__ = (UniqueConstraint("user_id", "comment_id", name="uq_comment_like_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    comment_id: Mapped[int] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()
    comment: Mapped["Comment"] = relationship()


class Like(Base):
    __tablename__ = "likes"
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_like_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="likes")
    post: Mapped[Post] = relationship(back_populates="likes")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id])


class Media(Base):
    """An uploaded file. ``stored_name`` is server-generated, never client input."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # The bytes themselves. A host with an ephemeral filesystem (the free Render
    # tier) loses `UPLOAD_DIR` on every redeploy while the database — typically
    # an external Postgres — keeps these rows, which is how every attachment
    # 404s at once. Keeping the bytes in the row makes media survive with the
    # data. Nullable so a row written before this column existed still loads and
    # can fall back to the file on disk.
    #
    # Deferred: the column is only read when a download actually needs it, so
    # listing a feed never pulls every attachment's bytes into memory. Do not
    # remove `deferred` — `serialize_media` never touches `blob`, so a feed
    # would otherwise load megabytes per post for nothing.
    blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alt_text: Mapped[str] = mapped_column(String(280), default="")
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Denormalised so the inbox can sort without joining every message.
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    participants: Mapped[list["ConversationParticipant"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ConversationParticipant(Base):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_participant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="participants")
    user: Mapped[User] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    sender: Mapped[User] = relationship()
    media: Mapped[list[Media]] = relationship(
        primaryjoin="Media.message_id == Message.id",
        foreign_keys="Media.message_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PasswordReset(Base):
    """A single-use password reset token.

    Only the SHA-256 of the token is stored, so a leaked database row cannot be
    replayed as a working link. ``used_at`` makes the token single-use and
    ``expires_at`` bounds how long a mail sitting in an inbox stays dangerous.
    """

    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()
