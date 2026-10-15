"""SQLAlchemy ORM models for the Pulse social platform."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
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
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
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

    author: Mapped[User] = relationship(back_populates="posts")
    comments: Mapped[list["Comment"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    likes: Mapped[list["Like"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    tags: Mapped[list[Tag]] = relationship(secondary=post_tags, back_populates="posts", lazy="selectin")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    post: Mapped[Post] = relationship(back_populates="comments")
    author: Mapped[User] = relationship(back_populates="comments")


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
