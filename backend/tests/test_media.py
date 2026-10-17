"""Tests for media upload, validation and authenticated delivery."""

import io
import os
import struct
import wave

import pytest

from app import storage
from app.config import settings

# A 1x1 PNG. Small, real, and enough to exercise the magic-byte path.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def upload(client, headers, data: bytes, filename: str = "x.png", alt: str = ""):
    return client.post(
        "/api/media",
        headers=headers,
        files={"file": (filename, io.BytesIO(data), "image/png")},
        data={"alt_text": alt},
    )


def test_upload_requires_authentication(client):
    assert upload(client, {}, PNG).status_code == 401


def test_upload_accepts_png(client, make_user):
    headers, _ = make_user("ada")
    response = upload(client, headers, PNG, alt="a dot")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "image"
    assert body["content_type"] == "image/png"
    assert body["size_bytes"] == len(PNG)
    assert body["alt_text"] == "a dot"
    assert body["url"] == f"/api/media/{body['id']}"


def test_upload_rejects_disguised_html(client, make_user):
    """A .png name over HTML content must not be accepted as an image."""
    headers, _ = make_user("ada")
    response = upload(client, headers, b"<html><script>alert(1)</script>", filename="evil.png")
    assert response.status_code == 415


def test_upload_rejects_svg(client, make_user):
    """SVG can carry script, so it is not an accepted upload type."""
    headers, _ = make_user("ada")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert upload(client, headers, svg, filename="x.svg").status_code == 415


def test_upload_rejects_empty(client, make_user):
    headers, _ = make_user("ada")
    assert upload(client, headers, b"").status_code == 400


def test_uploaded_filename_is_not_client_controlled(client, make_user, tmp_path, monkeypatch):
    """The stored name is generated, so traversal in the filename is inert."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    response = upload(client, headers, PNG, filename="../../../../etc/passwd.png")
    assert response.status_code == 201

    stored = os.listdir(tmp_path)
    assert len(stored) == 1
    name = stored[0]
    assert name.endswith(".png")
    assert "/" not in name and ".." not in name


def test_media_requires_auth_and_visibility(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    other, _ = make_user("grace")
    media_id = upload(client, headers, PNG).json()["id"]

    # Owner can read an unattached upload.
    assert client.get(f"/api/media/{media_id}", headers=headers).status_code == 200
    # A different user cannot.
    assert client.get(f"/api/media/{media_id}", headers=other).status_code == 404
    # An anonymous caller is rejected outright, before visibility is even checked.
    assert client.get(f"/api/media/{media_id}").status_code == 401


def test_media_becomes_public_once_attached_to_a_post(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    other, _ = make_user("grace")
    media = upload(client, headers, PNG, alt="diagram").json()

    post = client.post(
        "/api/posts",
        headers=headers,
        json={"body": "with a picture", "media_ids": [media["id"]]},
    )
    assert post.status_code == 201, post.text
    assert [m["id"] for m in post.json()["media"]] == [media["id"]]

    # Now anyone signed in can read it, since the post is public.
    assert client.get(f"/api/media/{media['id']}", headers=other).status_code == 200


def test_cannot_attach_another_users_upload(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    media = upload(client, ada, PNG).json()

    response = client.post(
        "/api/posts", headers=grace, json={"body": "steal", "media_ids": [media["id"]]}
    )
    assert response.status_code == 403


def test_cannot_reuse_an_attachment(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media = upload(client, headers, PNG).json()
    first = client.post("/api/posts", headers=headers, json={"body": "one", "media_ids": [media["id"]]})
    assert first.status_code == 201

    second = client.post("/api/posts", headers=headers, json={"body": "two", "media_ids": [media["id"]]})
    assert second.status_code == 409


def test_unknown_attachment_is_rejected(client, make_user):
    headers, _ = make_user("ada")
    response = client.post("/api/posts", headers=headers, json={"body": "x", "media_ids": [9999]})
    assert response.status_code == 400


def test_media_only_post_is_allowed(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media = upload(client, headers, PNG).json()
    response = client.post("/api/posts", headers=headers, json={"body": "", "media_ids": [media["id"]]})
    assert response.status_code == 201, response.text
    assert response.json()["body"] == ""
    assert len(response.json()["media"]) == 1


def test_sniff_identifies_common_types():
    assert storage.sniff(PNG[:64]).content_type == "image/png"
    assert storage.sniff(b"\xff\xd8\xff\xe0" + b"\x00" * 8).content_type == "image/jpeg"
    assert storage.sniff(b"GIF89a" + b"\x00" * 8).content_type == "image/gif"
    assert storage.sniff(b"RIFF\x00\x00\x00\x00WEBP").content_type == "image/webp"
    assert storage.sniff(b"\x00\x00\x00\x18ftypmp42").content_type == "video/mp4"
    assert storage.sniff(b"%PDF-1.7").content_type == "application/pdf"
    with pytest.raises(storage.UnsupportedMedia):
        storage.sniff(b"not a real file")


def wav_bytes(seconds: float = 0.2, rate: int = 8000) -> bytes:
    """A real WAV, written by the stdlib encoder rather than hand-built."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buffer.getvalue()


def webm_header(track_codec: bytes) -> bytes:
    """A WebM header whose `Tracks` element names one track's codec.

    Chrome writes `A_OPUS` for a voice note and `V_VP8` for a screen recording,
    and both files begin with the same EBML magic, so the codec id is the only
    thing in the header that separates them.
    """
    return (
        b"\x1a\x45\xdf\xa3"
        + b"\x00" * 12
        + b"\x16\x54\xae\x6b"
        + b"\x00" * 8
        + track_codec
        + b"\x00" * 32
    )


def iso_bmff(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18" + b"ftyp" + brand + b"\x00" * 32


def test_sniff_identifies_audio_types():
    """Voice notes are recognised, including the containers they share with video."""
    assert storage.sniff(wav_bytes()).kind == "audio"
    assert storage.sniff(wav_bytes()).content_type == "audio/wav"
    # 'OggS' capture pattern with an Opus identification header.
    assert storage.sniff(b"OggS\x00\x02" + b"\x00" * 20).content_type == "audio/ogg"
    assert storage.sniff(b"ID3\x03\x00\x00\x00" + b"\x00" * 8).content_type == "audio/mpeg"


def test_sniff_splits_webm_audio_from_webm_video():
    """The same EBML magic must resolve differently by the tracks it declares."""
    audio = storage.sniff(webm_header(b"A_OPUS"))
    video = storage.sniff(webm_header(b"V_VP8"))
    assert (audio.kind, audio.content_type) == ("audio", "audio/webm")
    assert (video.kind, video.content_type) == ("video", "video/webm")


def test_sniff_splits_mp4_audio_from_mp4_video():
    """Only audio-specific brands are audio; generic ones stay video.

    `mp42` and `isom` are ordinary MP4 video brands, so treating them as audio
    would misclassify every uploaded video as a voice note.
    """
    assert storage.sniff(iso_bmff(b"M4A ")).content_type == "audio/mp4"
    assert storage.sniff(iso_bmff(b"mp42")).content_type == "video/mp4"
    assert storage.sniff(iso_bmff(b"isom")).content_type == "video/mp4"


def test_audio_upload_is_accepted(client, make_user):
    headers, _ = make_user("ada")
    response = client.post(
        "/api/media",
        headers=headers,
        files={"file": ("note.wav", io.BytesIO(wav_bytes()), "audio/wav")},
        data={"alt_text": "voice note"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "audio"
    assert body["content_type"] == "audio/wav"
    assert body["url"] == f"/api/media/{body['id']}"


def test_audio_upload_rejects_html_disguised_as_audio(client, make_user):
    """The sniff decides, not the declared content type or the file name."""
    headers, _ = make_user("ada")
    response = client.post(
        "/api/media",
        headers=headers,
        files={"file": ("note.webm", io.BytesIO(b"<html><script>alert(1)</script>"), "audio/webm")},
    )
    assert response.status_code == 415


def test_audio_has_its_own_size_ceiling():
    """A voice note is bounded well below video, since it lands in a DB row."""
    assert storage.max_bytes_for("audio") < storage.max_bytes_for("video")
    assert storage.max_bytes_for("audio") > 0


def test_allowed_types_label_covers_every_accepted_type():
    """The 415 message names every type the upload routes will take.

    The label table is an override, not a mirror, so a type added to the
    allow-list without a friendly name must still appear rather than raising a
    KeyError from inside the error handler.
    """
    parts = storage.allowed_types_label().split(", ")
    assert len(parts) == len(storage.ALLOWED_TYPES)
    assert all(part.strip() for part in parts)

    # A type with no override still renders, using its subtype.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(storage.ALLOWED_TYPES, "audio/flac", "flac")
    try:
        assert "FLAC" in storage.allowed_types_label().split(", ")
    finally:
        monkeypatch.undo()


def test_every_sniffable_type_is_declared_allowed():
    """`sniff` cannot return a type the upload routes would then reject.

    Results are built from `ALLOWED_TYPES`, so this guards the one way that
    could drift: a signature added for a type nobody declared acceptable.
    """
    samples = [
        PNG[:64],
        b"\xff\xd8\xff\xe0" + b"\x00" * 8,
        b"GIF89a" + b"\x00" * 8,
        b"RIFF\x00\x00\x00\x00WEBP",
        b"\x00\x00\x00\x18ftypmp42",
        b"\x00\x00\x00\x18ftypM4A ",
        b"\x1a\x45\xdf\xa3" + b"\x00" * 12 + b"A_OPUS" + b"\x00" * 32,
        b"\x1a\x45\xdf\xa3" + b"\x00" * 12 + b"V_VP8" + b"\x00" * 32,
        wav_bytes(),
        b"OggS\x00\x02" + b"\x00" * 20,
        b"ID3\x03\x00\x00\x00" + b"\x00" * 8,
        b"%PDF-1.7",
        b"PK\x03\x04" + b"\x00" * 8,
    ]
    for sample in samples:
        result = storage.sniff(sample[: storage.SNIFF_BYTES])
        assert result.content_type in storage.ALLOWED_TYPES, result.content_type
        # The extension on disk comes from the same table, so the stored file
        # and the served content type can never disagree.
        assert result.extension == storage.ALLOWED_TYPES[result.content_type]
        assert result.kind == result.content_type.split("/", 1)[0]


def test_resolve_refuses_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    with pytest.raises(ValueError):
        storage.resolve("../escape.png")


def test_uploaded_bytes_are_stored_in_the_database(client, make_user, tmp_path, monkeypatch):
    """The row carries the bytes, so media survives a lost UPLOAD_DIR.

    The hosted demo runs on a free Render instance: the database is an external
    Postgres and outlives a redeploy, but the filesystem does not. If the bytes
    lived only on disk every attachment would 404 for every account after the
    next deploy, which is the failure this pins.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, me = make_user("ada")
    media_id = upload(client, headers, PNG).json()["id"]

    # Simulate a redeploy: the upload directory is gone.
    for name in os.listdir(tmp_path):
        os.unlink(os.path.join(tmp_path, name))

    response = client.get(f"/api/media/{media_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.content == PNG


def test_legacy_row_without_a_blob_falls_back_to_disk(client, make_user, tmp_path, monkeypatch):
    """A row written before the blob column still serves from its file."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, me = make_user("ada")
    media_id = upload(client, headers, PNG).json()["id"]

    from app.database import get_db
    from app.models import Media

    override = client.app.dependency_overrides[get_db]
    session = next(override())
    try:
        # Clear the blob to mimic a row that predates the column.
        session.get(Media, media_id).blob = None
        session.commit()
    finally:
        session.close()

    response = client.get(f"/api/media/{media_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.content == PNG


def test_missing_media_is_a_404_even_for_its_owner(client, make_user, tmp_path, monkeypatch):
    """Bytes lost with no database copy stay a 404, not a 500."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, me = make_user("ada")
    media_id = upload(client, headers, PNG).json()["id"]

    from app.database import get_db
    from app.models import Media

    override = client.app.dependency_overrides[get_db]
    session = next(override())
    try:
        session.get(Media, media_id).blob = None
        session.commit()
    finally:
        session.close()
    for name in os.listdir(tmp_path):
        os.unlink(os.path.join(tmp_path, name))

    assert client.get(f"/api/media/{media_id}", headers=headers).status_code == 404


def test_media_sends_a_validator_and_answers_304(client, make_user, tmp_path, monkeypatch):
    """A repeat load costs a 304, not the bytes again.

    Feeds are image-heavy, so re-fetching every avatar and attachment on each
    visit is most of the load. An ETag lets the browser reuse what it already
    has; this pins that the validator is present and honoured.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media_id = upload(client, headers, PNG).json()["id"]

    first = client.get(f"/api/media/{media_id}", headers=headers)
    assert first.status_code == 200
    etag = first.headers["ETag"]
    assert etag
    # The response is per-viewer, so a shared cache must key on the credential.
    assert "Authorization" in first.headers["Vary"]

    repeat = client.get(f"/api/media/{media_id}", headers={**headers, "If-None-Match": etag})
    assert repeat.status_code == 304
    assert repeat.content == b""


def test_media_revalidates_when_the_etag_differs(client, make_user, tmp_path, monkeypatch):
    """A stale validator must not be treated as a hit, or the wrong bytes show."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media_id = upload(client, headers, PNG).json()["id"]

    response = client.get(
        f"/api/media/{media_id}", headers={**headers, "If-None-Match": '"not-this-one"'}
    )
    assert response.status_code == 200
    assert response.content == PNG


def test_wildcard_if_none_match_is_not_a_hit(client, make_user, tmp_path, monkeypatch):
    """`If-None-Match: *` asserts "any copy", not "these bytes", so serve them."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media_id = upload(client, headers, PNG).json()["id"]

    response = client.get(f"/api/media/{media_id}", headers={**headers, "If-None-Match": "*"})
    assert response.status_code == 200
    assert response.content == PNG
