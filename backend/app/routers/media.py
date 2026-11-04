"""Media upload and authenticated delivery."""

import os
from collections.abc import Iterator

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import or_, select

from .. import images, storage
from ..deps import CurrentUser, DbSession, OptionalUser, serialize_media
from ..models import ConversationParticipant, Media, Message, Post, User
from ..schemas import MediaOut

router = APIRouter(prefix="/api/media", tags=["media"])

# The client sends a preview frame with a video. It is an image the client
# produced, so it is sniffed like any other upload and rejected if it is not a
# real image — a poster is served to whoever can read the video, and an
# ``image/svg+xml`` or an HTML payload smuggled through this field would be
# served back with its own content type.
MAX_POSTER_BYTES = 2 * 1024 * 1024


def _poster_from(data: bytes | None) -> tuple[str, bytes] | None:
    """Validate a client-supplied preview frame, or None if unusable.

    A poster is an enhancement: a bad or missing one must not fail the upload
    that carries it, or a client that cannot decode a frame would be unable to
    post video at all. Anything that does not sniff as a supported image is
    dropped silently.
    """
    if not data or len(data) > MAX_POSTER_BYTES:
        return None
    try:
        sniffed = storage.sniff(data[: storage.SNIFF_BYTES])
    except storage.UnsupportedMedia:
        return None
    if sniffed.kind != "image":
        return None
    # Posters are stored in the row like any other media, so they are optimized
    # too. A poster is a single video frame and needs nothing like a full-size
    # photo, which makes it the most rewarding thing to shrink.
    optimized, content_type = images.optimize_image(data, sniffed.content_type)
    return content_type, optimized


def _duration_ms(value: int | None) -> int | None:
    """A sane video length, or None.

    Clamped rather than rejected: the number only drives a badge, and a client
    reporting a nonsense length should not lose its upload over it.
    """
    if value is None or value <= 0:
        return None
    # 24 hours. Past this the value is a units mistake, not a long video.
    return min(value, 24 * 60 * 60 * 1000)


@router.post("", response_model=MediaOut, status_code=status.HTTP_201_CREATED)
async def upload(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
    alt_text: str = Form(""),
    duration_ms: int | None = Form(None),
    poster: UploadFile | None = File(None),
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

    # Shrink an image before anything is written. The original is accepted up to
    # the limit above and then re-encoded, so a client can keep uploading a
    # full-resolution photo while what lands in the database is a fraction of it.
    # The type is re-derived from the returned bytes, because re-encoding can
    # change the extension a re-download should carry.
    content_type = sniffed.content_type
    if sniffed.kind == "image":
        data, content_type = images.optimize_image(data, sniffed.content_type)

    stored_name = storage.store(data, storage.ALLOWED_TYPES.get(content_type, sniffed.extension))
    # Only a video carries a preview frame; an image is its own preview.
    poster_data = _poster_from(await poster.read()) if (poster and sniffed.kind == "video") else None
    media = Media(
        owner_id=user.id,
        stored_name=stored_name,
        content_type=content_type,
        kind=sniffed.kind,
        size_bytes=len(data),
        # The bytes live in the row, not only on disk. A host with an ephemeral
        # filesystem loses `UPLOAD_DIR` on every redeploy while the database
        # keeps the row, so the file alone is not a durable copy of an upload.
        blob=data,
        poster_content_type=poster_data[0] if poster_data else None,
        poster_blob=poster_data[1] if poster_data else None,
        duration_ms=_duration_ms(duration_ms) if sniffed.kind == "video" else None,
        alt_text=alt_text.strip()[:280],
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    return serialize_media([media])[0]


def _media_access(db, media: Media, viewer: User | None) -> tuple[bool, bool] | None:
    """Decide visibility and shared-cacheability in one round trip.

    Returns ``(visible, publicly_cacheable)``, or None when the caller may not
    read the media at all.

    The two questions are answered together because each is a query, and the
    database may be in another region: the media route previously issued up to
    three separate lookups (post, profile image, then post again for the cache
    header), each paying a full network round trip. Folding them into one SELECT
    is the difference between one hop and three.

    ``publicly_cacheable`` is true only for a post attachment or a profile
    picture. A direct-message attachment is neither, so it stays out of a shared
    cache — the same rule as before, now evaluated server-side.
    """
    # A post attachment is public the moment the post exists. One existence
    # check covers both the visibility answer and the cacheable answer here.
    if media.post_id is not None:
        exists = db.scalar(select(Post.id).where(Post.id == media.post_id).limit(1))
        # A row naming a post that no longer exists is treated as not visible.
        return (True, True) if exists is not None else None

    if media.message_id is not None:
        if viewer is None:
            return None
        # A direct message is private to its participants. Both checks are one
        # EXISTS over the join rather than a message fetch followed by a
        # participant lookup.
        participant = db.scalar(
            select(ConversationParticipant.id)
            .join(Message, Message.conversation_id == ConversationParticipant.conversation_id)
            .where(
                Message.id == media.message_id,
                ConversationParticipant.user_id == viewer.id,
            )
            .limit(1)
        )
        return (True, False) if participant is not None else None

    # Not attached to anything: it is only readable while it is somebody's
    # picture or header, and that case is shared-cacheable because it renders
    # beside every post the account owns.
    if is_public_profile_image(db, media):
        return True, True

    if viewer is None:
        return None
    return (True, False) if viewer.id == media.owner_id else None


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


def _poster_etag(media: Media) -> str:
    """The poster's own validator.

    Distinct from ``_etag`` because it names different bytes for the same id:
    sharing a validator between the video and its preview would let a client
    that cached one answer a conditional request for the other with 304.
    """
    return f'"{media.id}-{media.stored_name}-poster"'


def _is_immutable_cache_hit(media: Media, headers, etag: str | None = None) -> bool:
    """Whether the client already holds these exact bytes.

    Only an exact-match ETag counts. An `If-None-Match: *` is deliberately not
    treated as a hit: it asserts "any copy will do", which is not the same as
    holding these bytes, and answering 304 for it would show the wrong picture.

    ``etag`` overrides the default validator for routes serving something other
    than the video's own bytes.
    """
    candidate = headers.get("if-none-match")
    if not candidate:
        return False
    expected = etag or _etag(media)
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


@router.get("/{media_id}/poster")
def poster(media_id: int, db: DbSession, viewer: OptionalUser, request: Request) -> Response:
    """The stored preview frame for a video.

    Separate from the video's own route so a feed can ask for a few kilobytes
    instead of the whole file. Visibility is the *same* rule as the video's —
    ``_media_access`` is called here rather than a looser check, so a poster can
    never disclose a video the caller may not read.
    """
    media = db.get(Media, media_id)
    if media is None or media.poster_content_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    decision = _media_access(db, media, viewer)
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    _, publicly_cacheable = decision

    blob = media.poster_blob
    if not blob:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    headers = {
        # A poster is always safe to render inline: it was sniffed as an image
        # on the way in, so the stored type cannot be HTML or SVG.
        "Content-Type": media.poster_content_type,
        "X-Content-Type-Options": "nosniff",
        # Same reasoning as the video: public when the video is public, private
        # otherwise, and `Vary: Authorization` keeps a shared cache honest.
        "Cache-Control": (
            "public, max-age=86400" if publicly_cacheable else "private, max-age=86400"
        ),
        # Distinct from the video's ETag: same id, different bytes.
        "ETag": _poster_etag(media),
        "Vary": "Authorization",
    }
    if _is_immutable_cache_hit(media, request.headers, etag=_poster_etag(media)):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    return Response(content=blob, headers=headers)


@router.get("/{media_id}")
def download(
    media_id: int, db: DbSession, viewer: OptionalUser, request: Request
) -> Response:
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    # One round trip answers both "may this be read" and "may a shared cache
    # keep it". On a database in another region the round trips are what a
    # request costs, so they are counted.
    decision = _media_access(db, media, viewer)
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    _, publicly_cacheable = decision

    cache_control = (
        "public, max-age=86400" if publicly_cacheable else "private, max-age=86400"
    )

    # The ETag is derived from `stored_name`, which is already loaded, so a
    # conditional request can be answered before the blob is read. Serving the
    # 304 here means a repeat view never touches the bytes at all.
    etag = _etag(media)
    if _is_immutable_cache_hit(media, request.headers, etag=etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={
                "ETag": etag,
                "Cache-Control": cache_control,
                "Vary": "Authorization",
            },
        )

    # Read the blob once. `blob` is a deferred column, so the first attribute
    # access issues its own SELECT; reading it here rather than testing it and
    # then passing it to the response keeps that to a single round trip.
    blob = media.blob

    # Bytes stored in the row are the durable copy and are preferred. Older
    # rows have no blob, and a local deployment may still hold the file, so the
    # filesystem is a fallback rather than the only source.
    headers = {
        # Inline images/video so they render, but force a download for anything
        # else (PDF) and never let the browser sniff a different type.
        "Content-Disposition": _content_disposition(media),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": cache_control,
        # The bytes never change for an id, so a validator makes a repeat load a
        # 304 with no body. `Vary: Authorization` is required because the
        # response can be per-viewer: a shared cache must not hand one account's
        # bytes to another. Do not drop it when adding a shared cache.
        "ETag": etag,
        "Vary": "Authorization",
    }

    if blob is not None:
        return StreamingResponse(
            _stream_blob(blob),
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
