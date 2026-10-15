"""Tests for the profile cover photo (header image)."""

import io

from app.config import settings

from .test_media import PNG


def cover_upload(client, headers, data: bytes = PNG, filename: str = "cover.png"):
    return client.post(
        "/api/users/me/cover",
        headers=headers,
        files={"file": (filename, io.BytesIO(data), "image/png")},
    )


def test_cover_upload_requires_authentication(client):
    assert cover_upload(client, {}).status_code == 401


def test_cover_starts_empty(client, make_user):
    _, me = make_user("ada")
    assert me["cover_url"] is None


def test_cover_upload_sets_a_resolved_url(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    response = cover_upload(client, headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cover_url"] is not None
    assert body["cover_url"].startswith("/api/media/")

    # Persisted, not just echoed back on the upload response.
    assert client.get("/api/users/ada", headers=headers).json()["cover_url"] == body["cover_url"]


def test_cover_is_readable_by_other_users(client, make_user, tmp_path, monkeypatch):
    """A header image is shown on the public profile, so it cannot stay private.

    Regression guard: the media visibility check has to look at both the avatar
    and cover columns. Covering only one makes the image load for its owner and
    404 for everybody else, which reads as a broken profile.
    """
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    ada, _ = make_user("ada")
    other, _ = make_user("grace")

    cover_url = cover_upload(client, ada).json()["cover_url"]
    assert client.get(cover_url, headers=other).status_code == 200


def test_avatar_is_still_readable_after_cover_support(client, make_user, tmp_path, monkeypatch):
    """The avatar column must not have been dropped from the visibility check."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    ada, _ = make_user("ada")
    other, _ = make_user("grace")

    response = client.post(
        "/api/users/me/avatar",
        headers=ada,
        files={"file": ("me.png", io.BytesIO(PNG), "image/png")},
    )
    avatar_url = response.json()["avatar_url"]
    assert client.get(avatar_url, headers=other).status_code == 200


def test_cover_upload_rejects_a_non_image(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    response = cover_upload(client, headers, data=b"<html>nope</html>")
    assert response.status_code == 415


def test_cover_upload_rejects_a_disguised_video(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
    response = cover_upload(client, headers, data=mp4, filename="clip.mp4")
    assert response.status_code == 415


def test_cover_replace_deletes_the_previous_file(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    first = cover_upload(client, headers).json()["cover_url"]
    assert len(list(tmp_path.iterdir())) == 1

    second = cover_upload(client, headers).json()["cover_url"]
    assert second != first
    assert len(list(tmp_path.iterdir())) == 1


def test_cover_and_avatar_are_independent(client, make_user, tmp_path, monkeypatch):
    """Uploading a header must not disturb the profile picture, or vice versa."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    avatar = client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"file": ("me.png", io.BytesIO(PNG), "image/png")},
    ).json()
    cover = cover_upload(client, headers).json()

    assert avatar["avatar_url"] != cover["cover_url"]
    assert cover["avatar_url"] == avatar["avatar_url"]
    # Both files are on disk; neither upload evicted the other.
    assert len(list(tmp_path.iterdir())) == 2

    # Removing one leaves the other in place.
    cleared = client.delete("/api/users/me/avatar", headers=headers).json()
    assert cleared["avatar_url"] is None
    assert cleared["cover_url"] == cover["cover_url"]
    assert len(list(tmp_path.iterdir())) == 1


def test_cover_clear_reverts_to_gradient(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    cover_upload(client, headers)

    response = client.delete("/api/users/me/cover", headers=headers)
    assert response.status_code == 200
    assert response.json()["cover_url"] is None
    # The file is gone from disk, so removing a header reclaims the space.
    assert list(tmp_path.iterdir()) == []


def test_clearing_an_absent_cover_is_idempotent(client, make_user):
    headers, _ = make_user("ada")
    assert client.delete("/api/users/me/cover", headers=headers).json()["cover_url"] is None


def test_cover_is_exposed_to_moderators(client, make_user, make_admin, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    user_headers, _ = make_user("ada")
    admin_headers, _ = make_admin()
    cover_upload(client, user_headers)

    listing = client.get("/api/admin/users", headers=admin_headers).json()
    ada = next(item for item in listing["items"] if item["username"] == "ada")
    assert ada["cover_url"] is not None


def test_cover_can_be_set_by_patch(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    media = client.post(
        "/api/media",
        headers=headers,
        files={"file": ("c.png", io.BytesIO(PNG), "image/png")},
    ).json()

    response = client.patch("/api/users/ada", headers=headers, json={"cover_media_id": media["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["cover_url"] == f"/api/media/{media['id']}"


def test_patch_rejects_someone_elses_media_as_cover(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")

    media = client.post(
        "/api/media",
        headers=ada,
        files={"file": ("c.png", io.BytesIO(PNG), "image/png")},
    ).json()

    response = client.patch("/api/users/grace", headers=grace, json={"cover_media_id": media["id"]})
    assert response.status_code == 400


def test_patch_clears_cover_with_null(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    cover_upload(client, headers)

    response = client.patch("/api/users/ada", headers=headers, json={"cover_media_id": None})
    assert response.status_code == 200
    assert response.json()["cover_url"] is None
