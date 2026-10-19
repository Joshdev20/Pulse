"""Tests for media upload, validation and authenticated delivery."""

import io
import json
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

# An 8x8 JPEG, as the browser's `canvas.toDataURL('image/jpeg')` produces for a
# captured video frame. Real bytes, so it exercises the same magic-byte path a
# genuine poster takes.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb0043000a07070807060a"
    "0808080b0a0a0b0e18100e0d0d0e1d15161118231f2524221f2221262b372f26"
    "293429212230413134393b3e3e3e252e4449433c48373d3e3bffdb0043010a0b"
    "0b0e0d0e1c10101c3b2822283b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b"
    "3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3bffc0"
    "0011080008000803012200021101031101ffc4001f0000010501010101010100"
    "000000000000000102030405060708090a0bffc400b510000201030302040305"
    "0504040000017d01020300041105122131410613516107227114328191a10823"
    "42b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a"
    "838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7"
    "b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1"
    "f2f3f4f5f6f7f8f9faffc4001f01000301010101010101010100000000000001"
    "02030405060708090a0bffc400b5110002010204040304070504040001027700"
    "0102031104052131061241510761711322328108144291a1b1c109233352f015"
    "6272d10a162434e125f11718191a262728292a35363738393a43444546474849"
    "4a535455565758595a636465666768696a737475767778797a82838485868788"
    "898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4"
    "c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9"
    "faffda000c03010002110311003f00e6a8a28afb63d63fffd9"
)

# A minimal but real MP4 header: `ftyp` with the generic `isom` brand, which
# `sniff` classifies as video. Only the leading bytes matter to `sniff`, so this
# stands in for a real upload.
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 32

# A video-shaped payload comfortably larger than a poster, for the one test that
# asserts a poster is the smaller of the two. A real clip is megabytes; the
# fixture above is 44 bytes, which no preview image could undercut.
BIG_MP4 = MP4 + b"\x00" * 64_000


def upload_video(client, headers, poster=None, body=MP4, **form):
    files = {"file": ("clip.mp4", io.BytesIO(body), "video/mp4")}
    if poster is not None:
        files["poster"] = ("poster.jpg", io.BytesIO(poster), "image/jpeg")
    return client.post("/api/media", headers=headers, files=files, data=form)


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
    # Nor can an anonymous caller: an upload that is not yet attached to a post
    # is private to its owner, so the public routes must not reach it.
    assert client.get(f"/api/media/{media_id}").status_code == 404


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


# --- Video posters -----------------------------------------------------------
#
# A tile must not download a whole video to show a frame. The client captures
# one at upload time and sends it with the file, so these cover the path that
# makes a preview appear at once rather than after the download.


def test_video_poster_is_stored_and_served(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    response = upload_video(
        client, headers, poster=JPEG, body=BIG_MP4, alt_text="clip", duration_ms="10250"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "video"
    assert body["poster_url"] == f"/api/media/{body['id']}/poster"
    assert body["duration_ms"] == 10250

    # The poster is far smaller than the video it previews, which is the point:
    # this is the download the tile no longer has to make.
    poster = client.get(body["poster_url"], headers=headers)
    assert poster.status_code == 200
    assert poster.content == JPEG
    assert poster.headers["content-type"] == "image/jpeg"
    assert len(poster.content) < body["size_bytes"]


def test_poster_is_public_exactly_when_the_video_is(client, make_user, tmp_path, monkeypatch):
    """The poster reuses the video's own visibility rule, not a looser one."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    media = upload_video(client, ada, poster=JPEG).json()
    path = media["poster_url"]

    # Unattached: private to its owner, so it cannot be probed for by id.
    assert client.get(path, headers=ada).status_code == 200
    assert client.get(path, headers=grace).status_code == 404
    assert client.get(path).status_code == 404

    # Attached to a post: readable by anyone, like the post.
    post = client.post("/api/posts", headers=ada, json={"body": "clip", "media_ids": [media["id"]]})
    assert post.status_code == 201, post.text
    assert client.get(path, headers=grace).status_code == 200
    assert client.get(path).status_code == 200


def test_poster_has_its_own_etag(client, make_user, tmp_path, monkeypatch):
    """A poster's validator must not answer for the video, or vice versa.

    They are different bytes under the same id, so a shared validator would let
    a client that cached one be told 304 for the other and render the wrong
    thing.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media = upload_video(client, headers, poster=JPEG).json()
    poster_etag = client.get(media["poster_url"], headers=headers).headers["etag"]
    video_etag = client.get(media["url"], headers=headers).headers["etag"]
    assert poster_etag != video_etag

    # Revalidating the poster with its own validator is a 304 with no body.
    repeat = client.get(media["poster_url"], headers={**headers, "If-None-Match": poster_etag})
    assert repeat.status_code == 304
    assert repeat.content == b""

    # The same validator against the video must not be treated as a hit.
    wrong = client.get(media["url"], headers={**headers, "If-None-Match": poster_etag})
    assert wrong.status_code == 200
    assert wrong.content == MP4


def test_poster_cache_directive_follows_the_video(client, make_user, tmp_path, monkeypatch):
    """A public poster may be cached by a shared cache; a private one may not."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media = upload_video(client, headers, poster=JPEG).json()

    # Unattached, so private to the owner.
    private = client.get(media["poster_url"], headers=headers)
    assert private.headers["cache-control"].startswith("private")
    assert "Authorization" in private.headers["Vary"]

    client.post("/api/posts", headers=headers, json={"body": "x", "media_ids": [media["id"]]})
    public = client.get(media["poster_url"], headers=headers)
    assert public.headers["cache-control"].startswith("public")


def test_non_image_poster_is_dropped(client, make_user, tmp_path, monkeypatch):
    """A poster is sniffed like any upload, so script-bearing types are refused.

    It is served back to whoever can read the video, so keeping an SVG or an
    HTML payload here would be stored XSS under the API's own origin.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    for payload in (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', b"<html>x</html>"):
        response = upload_video(client, headers, poster=payload)
        # The upload itself still succeeds — a poster is an enhancement — but
        # the payload is not kept.
        assert response.status_code == 201, response.text
        assert response.json()["poster_url"] is None
        path = f"/api/media/{response.json()['id']}/poster"
        assert client.get(path, headers=headers).status_code == 404


def test_poster_is_optional(client, make_user, tmp_path, monkeypatch):
    """A client that cannot decode a frame must still be able to upload video."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    body = upload_video(client, headers).json()
    assert body["kind"] == "video"
    assert body["poster_url"] is None
    assert body["duration_ms"] is None
    assert client.get(f"/api/media/{body['id']}/poster", headers=headers).status_code == 404


def test_image_uploads_never_get_a_poster(client, make_user, tmp_path, monkeypatch):
    """An image is its own preview, so a poster field on one is ignored."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    response = client.post(
        "/api/media",
        headers=headers,
        files={
            "file": ("x.png", io.BytesIO(PNG), "image/png"),
            "poster": ("poster.jpg", io.BytesIO(JPEG), "image/jpeg"),
        },
        data={"alt_text": "a dot", "duration_ms": "5000"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["poster_url"] is None
    assert response.json()["duration_ms"] is None


def test_nonsense_duration_is_dropped_not_fatal(client, make_user, tmp_path, monkeypatch):
    """The length only drives a badge, so a bad value must not lose the upload."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    assert upload_video(client, headers, duration_ms="0").json()["duration_ms"] is None
    assert upload_video(client, headers, duration_ms="-5").json()["duration_ms"] is None
    # Absurd values are clamped rather than stored as reported.
    clamped = upload_video(client, headers, duration_ms="99999999999").json()["duration_ms"]
    assert clamped == 24 * 60 * 60 * 1000


def test_oversized_poster_is_dropped(client, make_user, tmp_path, monkeypatch):
    """The poster field cannot be used to push an unbounded blob into a row."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    huge = JPEG + b"\x00" * (2 * 1024 * 1024)
    response = upload_video(client, headers, poster=huge)
    assert response.status_code == 201, response.text
    assert response.json()["poster_url"] is None


def test_feed_response_does_not_load_poster_bytes(client, make_user, tmp_path, monkeypatch):
    """Listing a feed must not pull every preview image into memory.

    `poster_url` is built from the stored content type rather than the bytes,
    and `poster_blob` is deferred. This asserts the property that deferral
    exists for: a post listing carries the URL and no image data.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    media = upload_video(client, headers, poster=JPEG).json()
    client.post("/api/posts", headers=headers, json={"body": "clip", "media_ids": [media["id"]]})

    listed = client.get("/api/posts", headers=headers).json()["items"][0]["media"][0]
    assert listed["poster_url"] == media["poster_url"]
    assert "poster_blob" not in listed
    # The response is a URL, not the image: nothing of the poster's size.
    assert len(json.dumps(listed)) < len(JPEG)
