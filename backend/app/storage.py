"""Content-addressed-ish local storage for uploaded media.

Files are written to ``settings.upload_dir`` under a random name. The client
supplied filename and content type are never trusted: the type is derived from
the file's magic bytes and the extension from that derived type, so a caller
cannot land an ``.html`` or ``.svg`` payload in the upload directory.
"""

import os
import secrets
from dataclasses import dataclass

from .config import settings

# Types the platform accepts, mapped to the extension used on disk. This is the
# single source of truth: every `Sniffed` result is built from it, so a type that
# `sniff` can return is by construction one the upload routes accept.
ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/webm": "webm",
    "application/pdf": "pdf",
    "application/zip": "zip",
}

# Magic-byte prefixes, checked in order. Containers that share a prefix are
# resolved before this list is consulted, so `RIFF`, `ftyp` and the EBML magic
# do not appear here.
_SIGNATURES: list[tuple[str, bytes, int]] = [
    # (content_type, magic, offset)
    ("image/jpeg", b"\xff\xd8\xff", 0),
    ("image/png", b"\x89PNG\r\n\x1a\n", 0),
    ("image/gif", b"GIF87a", 0),
    ("image/gif", b"GIF89a", 0),
    ("audio/ogg", b"OggS", 0),
    ("audio/mpeg", b"ID3", 0),
    ("application/pdf", b"%PDF-", 0),
    ("application/zip", b"PK\x03\x04", 0),
]

# How many leading bytes `sniff` may inspect. Most types are decided by the first
# dozen bytes, but telling an audio WebM from a video one means reading into the
# `Tracks` element, so callers hand over this much of the file's start.
SNIFF_BYTES = 4096

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 32 * 1024 * 1024
# A voice note is short by nature, so it gets a much smaller ceiling than a
# video: a minute of Opus is under a megabyte, and this bounds what a client
# can push into a database row.
MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024


class UnsupportedMedia(Exception):
    """Raised when an upload is not a type the platform accepts."""


@dataclass(frozen=True)
class Sniffed:
    kind: str
    content_type: str
    extension: str


# Major brands that identify an MP4/ISO-BMFF file as audio-only rather than
# video. A voice note recorded in Safari is an `ftyp` box like any MP4, so the
# major brand is the only thing in the header that separates the two. Only the
# audio-specific brands are listed: `isom` and `mp42` are generic containers that
# ordinary MP4 video also uses, so treating them as audio would misclassify
# every uploaded video.
_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P "}


def _is_audio_webm(head: bytes) -> bool:
    """Whether a WebM/Matroska header describes an audio-only stream.

    Chrome records voice notes as ``audio/webm`` and screen video as
    ``video/webm``, and both begin with the same EBML magic. The header carries
    a ``Tracks`` element naming each track's codec, so this looks for an audio
    codec id. Detection is deliberately positive: a file is audio only when an
    audio codec is actually present, so a video whose track list falls outside
    the inspected window is still treated as video rather than being downgraded
    to a voice note.
    """
    window = head[:SNIFF_BYTES]
    return any(codec in window for codec in (b"A_OPUS", b"A_VORBIS", b"A_AAC"))


def _sniffed(content_type: str) -> Sniffed:
    """Build a result for a supported content type, rejecting anything else.

    `ALLOWED_TYPES` is consulted here rather than at the call sites, so a
    signature cannot be added without also declaring the type acceptable.
    """
    extension = ALLOWED_TYPES.get(content_type)
    if extension is None:
        raise UnsupportedMedia(f"Unsupported file type: {content_type}")
    return Sniffed(content_type.split("/", 1)[0], content_type, extension)


def sniff(head: bytes) -> Sniffed:
    """Identify an upload from its leading bytes.

    ``head`` should be the file's first ``SNIFF_BYTES`` bytes: most types are
    decided by the first few, but separating an audio WebM from a video one
    means reading into the ``Tracks`` element, which sits further in.
    """
    # RIFF is shared by WebP and WAV; the four bytes at offset 8 pick one.
    if head[:4] == b"RIFF":
        if head[8:12] == b"WEBP":
            return _sniffed("image/webp")
        if head[8:12] == b"WAVE":
            return _sniffed("audio/wav")

    # ISO-BMFF: `ftyp` at offset 4. The major brand at offset 8 says whether
    # this is an audio-only recording or a video. Only audio-specific brands
    # count as audio; a generic brand like `mp42` or `isom` is treated as video,
    # which is what an ordinary MP4 upload is.
    if head[4:8] == b"ftyp":
        if head[8:12] in _AUDIO_BRANDS:
            return _sniffed("audio/mp4")
        return _sniffed("video/mp4")

    # EBML: WebM and Matroska share the magic, so the track list decides.
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return _sniffed("audio/webm" if _is_audio_webm(head) else "video/webm")

    for content_type, magic, offset in _SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            return _sniffed(content_type)

    raise UnsupportedMedia("Unsupported file type")


# Friendly names for the accepted types. This is an override table, not a
# mirror of `ALLOWED_TYPES`: a type with no entry falls back to its subtype, so
# adding one to the allow-list cannot turn this message into a KeyError.
_TYPE_LABELS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WebP",
    "video/mp4": "MP4",
    "video/webm": "WebM",
    "audio/ogg": "Ogg",
    "audio/wav": "WAV",
    "audio/mpeg": "MP3",
    "audio/mp4": "M4A",
    "audio/webm": "WebM audio",
    "application/pdf": "PDF",
    "application/zip": "ZIP",
}


def allowed_types_label() -> str:
    """Human-readable list of accepted types, derived from the allow-list.

    Generated rather than written out at each call site, so adding a supported
    type cannot leave an error message advertising the old set.
    """
    return ", ".join(
        _TYPE_LABELS.get(content_type, content_type.split("/", 1)[-1].upper())
        for content_type in ALLOWED_TYPES
    )


def max_bytes_for(kind: str) -> int:
    if kind == "image":
        return MAX_IMAGE_BYTES
    if kind == "video":
        return MAX_VIDEO_BYTES
    if kind == "audio":
        return MAX_AUDIO_BYTES
    return MAX_FILE_BYTES


def ensure_upload_dir() -> str:
    path = settings.upload_dir
    os.makedirs(path, exist_ok=True)
    return path


def store(data: bytes, extension: str) -> str:
    """Persist ``data`` under a random name and return the stored filename.

    The name is generated locally, so a hostile ``filename`` from the client
    can never influence the path that gets written.
    """
    directory = ensure_upload_dir()
    name = f"{secrets.token_hex(16)}.{extension}"
    path = os.path.join(directory, name)
    # O_EXCL so a name collision can never silently overwrite an existing file.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(handle, "wb") as file:
        file.write(data)
    return name


def resolve(stored_name: str) -> str:
    """Map a stored filename to an absolute path, refusing anything else.

    ``stored_name`` always comes from the database rather than the request, but
    the containment check keeps a corrupted row from becoming a traversal.
    """
    directory = os.path.realpath(ensure_upload_dir())
    path = os.path.realpath(os.path.join(directory, stored_name))
    if path != directory and not path.startswith(directory + os.sep):
        raise ValueError("Path escapes the upload directory")
    return path


def delete(stored_name: str) -> None:
    try:
        os.unlink(resolve(stored_name))
    except (FileNotFoundError, ValueError):
        pass
