"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    created_at: datetime
    follower_count: int = 0
    following_count: int = 0
    post_count: int = 0
    is_following: bool = False


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
    created_at: datetime
    author: UserPublic


class PostCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class PostUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    created_at: datetime
    edited_at: datetime | None
    author: UserPublic
    tags: list[TagOut] = []
    like_count: int = 0
    comment_count: int = 0
    liked_by_me: bool = False


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    read: bool
    created_at: datetime
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
