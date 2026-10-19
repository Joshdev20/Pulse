"""Media upload and authenticated delivery."""

import os
from collections.abc import Iterator

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import or_, select

from .. import storage
from ..deps import CurrentUser, DbSession, OptionalUser, serialize_media
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
        sniffed = storage.sniff(data[: storage.SNIFF_BYTES])
    except storage.UnsupportedMedia:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type. Allowed: {storage.allowed_types_label()}",
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
        # The bytes live in the row, not only on disk. A host with an ephemeral
        # filesystem loses `UPLOAD_DIR` on every redeploy while the database
        # keeps the row, so the file alone is not a durable copy of an upload.
        blob=data,
        alt_text=alt_text.strip()[:280],
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    return serialize_media([media])[0]


def _visible_to(db, media: Media, viewer: User | None) -> bool:
    """Media is readable by anyone who can read the post or message it belongs to.

    An attachment not yet linked to a post or message is private to its owner,
    so an upload cannot be probed for by guessing ids.

    ``viewer`` is None for an anonymous visitor. Post attachments and profile
    pictures are public, because the posts and profiles they belong to are now
    publicly readable; anything reached through a direct message is not, and
    neither is an unattached upload.
    """
    if media.post_id is not None:
        return db.get(Post, media.post_id) is not None

    if media.message_id is not None:
        # The one boundary that must not move. A direct message is private to
        # its participants, so an anonymous visitor is refused outright rather
        # than falling through to a later branch.
        if viewer is None:
            return False
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

    if viewer is None:
        return False
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


def _content_disposition(media: Media) -> str:
    extension = media.stored_name.rsplit(".", 1)[-1]
    return f'inline; filename="{media.id}.{extension}"'


def _etag(media: Media) -> str:
    """A validator that changes whenever the bytes could have.

    Media rows are immutable once written — a replacement upload is a new row —
    so the id plus the stored name identifies one exact byte sequence.
    """
    return f'"{media.id}-{media.stored_name}"'


def _is_immutable_cache_hit(media: Media, headers) -> bool:
    """Whether the client already holds these exact bytes.

    Only an exact-match ETag counts. An `If-None-Match: *` is deliberately not
    treated as a hit: it asserts "any copy will do", which is not the same as
    holding these bytes, and answering 304 for it would show the wrong picture.
    """
    candidate = headers.get("if-none-match")
    if not candidate:
        return False
    expected = _etag(media)
    return any(part.strip() in (expected, expected[1:-1]) for part in candidate.split(","))


def _stream_blob(data: bytes, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """Yield a loaded blob in slices.

    A video can be tens of megabytes, and a plain `Response(content=blob)` makes
    Starlette hold the whole buffer again while rendering it. Slicing keeps peak
    memory near one copy instead of two, which matters on a small instance where
    several people may be watching a video at once.
    """
    view = memoryview(data)
    for start in range(0, len(view), chunk_size):
        yield bytes(view[start : start + chunk_size])


@router.get("/{media_id}")
def download(
    media_id: int, db: DbSession, viewer: OptionalUser, request: Request
) -> Response:
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    if not _visible_to(db, media, viewer):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    # Public media (a post attachment or a profile picture) may be cached by a
    # shared cache; everything else is per-viewer and must not be. Deriving this
    # rather than assuming keeps a direct-message image out of a CDN even if the
    # visibility rule above ever changes.
    publicly_cacheable = media.post_id is not None or is_public_profile_image(db, media)

    # Bytes stored in the row are the durable copy and are preferred. Older
    # rows have no blob, and a local deployment may still hold the file, so the
    # filesystem is a fallback rather than the only source.
    headers = {
        # Inline images/video so they render, but force a download for anything
        # else (PDF) and never let the browser sniff a different type.
        "Content-Disposition": _content_disposition(media),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": (
            "public, max-age=86400" if publicly_cacheable else "private, max-age=86400"
        ),
        # The bytes never change for an id, so a validator makes a repeat load a
        # 304 with no body. `Vary: Authorization` is required because the
        # response can be per-viewer: a shared cache must not hand one account's
        # bytes to another. Do not drop it when adding a shared cache.
        "ETag": _etag(media),
        "Vary": "Authorization",
    }
    if _is_immutable_cache_hit(media, request.headers):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    if media.blob is not None:
        return StreamingResponse(
            _stream_blob(media.blob),
            media_type=media.content_type,
            headers=headers,
        )

    try:
        path = storage.resolve(media.stored_name)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found") from None
    if not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    return FileResponse(path, media_type=media.content_type, headers=headers)


def backfill_blobs(db) -> int:
    """Copy any on-disk upload that has no database copy into its row.

    Best-effort repair for a database that predates the ``blob`` column: on a
    host that still has its files this preserves them before a redeploy loses
    them, and on one that has already lost them it is a no-op. Never raises —
    startup must not fail because a file could not be read.
    """
    repaired = 0
    rows = db.scalars(select(Media).where(Media.blob.is_(None)).order_by(Media.id)).all()
    for media in rows:
        try:
            path = storage.resolve(media.stored_name)
            if not os.path.exists(path):
                continue
            with open(path, "rb") as file:
                media.blob = file.read()
            repaired += 1
        except (ValueError, OSError):
            continue
    if repaired:
        db.commit()
    return repaired
