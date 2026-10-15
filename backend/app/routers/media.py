"""Media upload and authenticated delivery."""

import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select

from .. import storage
from ..deps import CurrentUser, DbSession, serialize_media
from ..models import Media, Post, User
from ..schemas import MediaOut

router = APIRouter(prefix="/api/media", tags=["media"])


@router.post("", response_model=MediaOut, status_code=status.HTTP_201_CREATED)
async def upload(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
    alt_text: str = Form(""),
) -> MediaOut:
    """Accept one file, identify it by content, and store it under a random name."""
    # Read one byte past the largest allowed size so an oversized body is
    # detected without buffering an unbounded stream into memory.
    limit = storage.max_bytes_for("video") + 1
    data = await file.read(limit)
    if len(data) > storage.max_bytes_for("video"):
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {storage.MAX_VIDEO_BYTES // (1024 * 1024)}MB limit",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    try:
        sniffed = storage.sniff(data[:64])
    except storage.UnsupportedMedia:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Unsupported file type. Allowed: JPEG, PNG, GIF, WebP, MP4, WebM, PDF",
        ) from None

    if len(data) > storage.max_bytes_for(sniffed.kind):
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"{sniffed.kind.title()} files are limited to "
            f"{storage.max_bytes_for(sniffed.kind) // (1024 * 1024)}MB",
        )

    stored_name = storage.store(data, sniffed.extension)
    media = Media(
        owner_id=user.id,
        stored_name=stored_name,
        content_type=sniffed.content_type,
        kind=sniffed.kind,
        size_bytes=len(data),
        alt_text=alt_text.strip()[:280],
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    return serialize_media([media])[0]


def _visible_to(db, media: Media, viewer: User) -> bool:
    """Media is readable by anyone who can read the post or message it belongs to.

    An attachment not yet linked to a post or message is private to its owner,
    so an upload cannot be probed for by guessing ids.
    """
    if media.post_id is not None:
        return db.get(Post, media.post_id) is not None

    if media.message_id is not None:
        from ..models import ConversationParticipant, Message

        message = db.get(Message, media.message_id)
        if message is None:
            return False
        return (
            db.scalar(
                select(ConversationParticipant.id).where(
                    ConversationParticipant.conversation_id == message.conversation_id,
                    ConversationParticipant.user_id == viewer.id,
                )
            )
            is not None
        )

    if is_public_profile_image(db, media):
        return True

    return viewer.id == media.owner_id


def is_public_profile_image(db, media: Media) -> bool:
    """Whether ``media`` is currently in use as somebody's picture or header.

    These appear beside every post and profile the account owns, so they are
    readable by any signed-in user. The check is what keeps that exception
    narrow: an upload that is *not* a profile image stays private to its owner
    until it is attached to a post or message. Both columns have to be checked
    here — missing one means the image loads for its owner and 404s for
    everyone else, which looks like a broken profile rather than a permissions
    problem.
    """
    return (
        db.scalar(
            select(User.id)
            .where(or_(User.avatar_media_id == media.id, User.cover_media_id == media.id))
            .limit(1)
        )
        is not None
    )


@router.get("/{media_id}")
def download(media_id: int, db: DbSession, viewer: CurrentUser) -> FileResponse:
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    if not _visible_to(db, media, viewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    try:
        path = storage.resolve(media.stored_name)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found") from None
    if not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    return FileResponse(
        path,
        media_type=media.content_type,
        # Inline images/video so they render, but force a download for anything
        # else (PDF) and never let the browser sniff a different type.
        headers={
            "Content-Disposition": f'inline; filename="{media.id}.{media.stored_name.rsplit(".", 1)[-1]}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
        },
    )
