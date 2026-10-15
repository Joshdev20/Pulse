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

# Uploads are small enough to sniff entirely in memory for the leading bytes.
_SIGNATURES: list[tuple[str, str, bytes, int]] = [
    # (kind, extension, magic, offset)
    ("image", "jpg", b"\xff\xd8\xff", 0),
    ("image", "png", b"\x89PNG\r\n\x1a\n", 0),
    ("image", "gif", b"GIF87a", 0),
    ("image", "gif", b"GIF89a", 0),
    ("image", "webp", b"RIFF", 0),  # 'WEBP' is checked at offset 8
    ("video", "mp4", b"ftyp", 4),
    ("video", "webm", b"\x1a\x45\xdf\xa3", 0),
    ("application", "pdf", b"%PDF-", 0),
    ("application", "zip", b"PK\x03\x04", 0),
]

# Types the platform accepts, mapped to the extension used on disk.
ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "application/pdf": "pdf",
}

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024


class UnsupportedMedia(Exception):
    """Raised when an upload is not a type the platform accepts."""


@dataclass(frozen=True)
class Sniffed:
    kind: str
    content_type: str
    extension: str


def sniff(head: bytes) -> Sniffed:
    """Identify an upload from its leading bytes.

    ``head`` only needs to be the first few dozen bytes; passing more is fine.
    """
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Sniffed("image", "image/webp", "webp")

    for kind, extension, magic, offset in _SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            content_type = {
                "jpg": "image/jpeg",
                "png": "image/png",
                "gif": "image/gif",
                "webp": "image/webp",
                "mp4": "video/mp4",
                "webm": "video/webm",
                "pdf": "application/pdf",
                "zip": "application/zip",
            }[extension]
            return Sniffed(kind, content_type, extension)

    raise UnsupportedMedia("Unsupported file type")


def max_bytes_for(kind: str) -> int:
    if kind == "image":
        return MAX_IMAGE_BYTES
    if kind == "video":
        return MAX_VIDEO_BYTES
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
