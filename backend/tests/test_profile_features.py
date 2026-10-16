"""Tests for profile pictures, the verified badge and password reset."""

import io

from app.config import settings
from app.database import get_db
from app.models import PasswordReset, User

from .test_media import PNG


def avatar_upload(client, headers, data: bytes = PNG, filename: str = "me.png"):
    return client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"file": (filename, io.BytesIO(data), "image/png")},
    )


# ---------------------------------------------------------------------------
# Profile pictures
# ---------------------------------------------------------------------------


def test_avatar_upload_requires_authentication(client):
    assert avatar_upload(client, {}).status_code == 401


def test_avatar_upload_sets_a_resolved_url(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, me = make_user("ada")
    assert me["avatar_url"] is None

    response = avatar_upload(client, headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["avatar_url"] == f"/api/media/{body['avatar_url'].rsplit('/', 1)[-1]}"

    # The new picture is visible to another user, since an avatar travels with
    # every post and profile the account appears in.
    other, _ = make_user("grace")
    media_id = body["avatar_url"].rsplit("/", 1)[-1]
    assert client.get(f"/api/media/{media_id}", headers=other).status_code == 200


def test_avatar_survives_a_lost_upload_directory(client, make_user, tmp_path, monkeypatch):
    """A profile picture keeps resolving after the filesystem is wiped.

    The picture lives in the same table as post media, so on a host whose disk
    is ephemeral (the free Render tier) it has to come from the database row or
    every other user sees a broken avatar after a redeploy.
    """
    import os

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, me = make_user("ada")
    media_id = avatar_upload(client, headers).json()["avatar_url"].rsplit("/", 1)[-1]

    for name in os.listdir(tmp_path):
        os.unlink(os.path.join(tmp_path, name))

    other, _ = make_user("grace")
    response = client.get(f"/api/media/{media_id}", headers=other)
    assert response.status_code == 200, response.text
    assert response.content == PNG


def test_avatar_upload_rejects_a_non_image(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    response = avatar_upload(client, headers, data=b"<html>not an image</html>", filename="x.png")
    assert response.status_code == 415


def test_avatar_upload_rejects_a_disguised_video(client, make_user, tmp_path, monkeypatch):
    """A video is a supported upload but must not be accepted as a picture."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
    response = avatar_upload(client, headers, data=mp4, filename="clip.mp4")
    assert response.status_code == 415


def test_avatar_replace_deletes_the_previous_file(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")

    first = avatar_upload(client, headers).json()["avatar_url"].rsplit("/", 1)[-1]
    assert len(list(tmp_path.iterdir())) == 1

    second = avatar_upload(client, headers).json()["avatar_url"].rsplit("/", 1)[-1]
    assert second != first
    # The superseded picture is gone from disk, so uploads do not accumulate.
    assert len(list(tmp_path.iterdir())) == 1


def test_avatar_clear_reverts_to_initials(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    avatar_upload(client, headers)

    response = client.delete("/api/users/me/avatar", headers=headers)
    assert response.status_code == 200
    assert response.json()["avatar_url"] is None
    assert list(tmp_path.iterdir()) == []


def test_profile_cannot_point_at_another_users_upload(client, make_user, tmp_path, monkeypatch):
    """Setting avatar_media_id to someone else's media must be refused."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    owner, _ = make_user("ada")
    attacker, _ = make_user("mallory")

    response = client.post(
        "/api/media",
        headers=owner,
        files={"file": ("x.png", io.BytesIO(PNG), "image/png")},
    )
    media_id = response.json()["id"]

    patched = client.patch(
        f"/api/users/mallory", headers=attacker, json={"avatar_media_id": media_id}
    )
    assert patched.status_code == 400


def test_avatar_url_appears_on_posts_and_in_search(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    headers, _ = make_user("ada")
    url = avatar_upload(client, headers).json()["avatar_url"]

    client.post("/api/posts", headers=headers, json={"body": "hello"})
    post = client.get("/api/feed?scope=all", headers=headers).json()["items"][0]
    assert post["author"]["avatar_url"] == url

    found = client.get("/api/search/users?q=ada", headers=headers).json()
    assert found[0]["avatar_url"] == url


# ---------------------------------------------------------------------------
# Verified badge
# ---------------------------------------------------------------------------


def _promote(client, user_id: int) -> None:
    session = next(client.app.dependency_overrides[get_db]())
    try:
        user = session.get(User, user_id)
        user.is_admin = True
        session.commit()
    finally:
        session.close()


def test_users_start_unverified(client, make_user):
    _, me = make_user("ada")
    assert me["is_verified"] is False


def test_admin_can_verify_and_unverify(client, make_user):
    admin_headers, admin = make_user("root")
    _promote(client, admin["id"])
    target_headers, target = make_user("ada")

    granted = client.post(
        f"/api/admin/users/{target['id']}/verify", headers=admin_headers, json={"is_verified": True}
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["is_verified"] is True
    assert granted.json()["verified_at"] is not None

    # The badge shows up on the public profile the moment it is granted.
    assert client.get("/api/users/ada", headers=target_headers).json()["is_verified"] is True

    revoked = client.post(
        f"/api/admin/users/{target['id']}/verify", headers=admin_headers, json={"is_verified": False}
    )
    assert revoked.json()["is_verified"] is False
    assert revoked.json()["verified_at"] is None


def test_verify_requires_an_admin(client, make_user):
    headers, target = make_user("ada")
    response = client.post(
        f"/api/admin/users/{target['id']}/verify", headers=headers, json={"is_verified": True}
    )
    assert response.status_code == 403


def test_verification_is_not_self_serveable(client, make_user):
    """There must be no non-admin route that grants the badge."""
    headers, _ = make_user("ada")
    response = client.patch("/api/users/ada", headers=headers, json={"is_verified": True})
    # is_verified is not an editable field, so it is ignored rather than applied.
    assert response.status_code == 200
    assert response.json()["is_verified"] is False


def test_admin_user_list_reports_verification(client, make_user):
    admin_headers, admin = make_user("root")
    _promote(client, admin["id"])
    _, target = make_user("ada")
    client.post(
        f"/api/admin/users/{target['id']}/verify", headers=admin_headers, json={"is_verified": True}
    )

    listed = client.get("/api/admin/users?q=ada", headers=admin_headers).json()["items"]
    assert listed[0]["is_verified"] is True


def test_admin_user_list_carries_the_avatar_url(client, make_user, tmp_path, monkeypatch):
    """The moderation table renders the same avatar as everywhere else."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    admin_headers, admin = make_user("root")
    _promote(client, admin["id"])
    target_headers, target = make_user("ada")
    url = avatar_upload(client, target_headers).json()["avatar_url"]

    listed = client.get("/api/admin/users?q=ada", headers=admin_headers).json()["items"]
    assert listed[0]["avatar_url"] == url
    assert listed[0]["id"] == target["id"]


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


def test_forgot_password_issues_a_token_when_no_mail_is_configured(
    client, make_user, monkeypatch
):
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "expose_reset_token", False)
    make_user("ada")

    response = client.post("/api/auth/forgot-password", json={"email": "ada@example.com"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reset_token"]
    assert "token=" in body["reset_url"]


def test_forgot_password_hides_the_token_when_mail_is_configured(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    make_user("ada")

    body = client.post("/api/auth/forgot-password", json={"email": "ada@example.com"}).json()
    assert body["reset_token"] is None
    assert body["reset_url"] is None


def test_forgot_password_does_not_reveal_whether_an_email_exists(client, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    known = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == 200
    # Identical shape whether or not the address is registered.
    assert known.json()["detail"] == (
        "If that email is registered, a reset link has been sent."
    )


def test_reset_password_changes_the_password_and_signs_in(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    make_user("ada")
    token = client.post(
        "/api/auth/forgot-password", json={"email": "ada@example.com"}
    ).json()["reset_token"]

    response = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "brand-new-pass"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]

    # The old password no longer works and the new one does.
    assert client.post(
        "/api/auth/login", json={"username": "ada", "password": "password123"}
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "ada", "password": "brand-new-pass"}
    ).status_code == 200


def test_reset_token_is_single_use(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    make_user("ada")
    token = client.post(
        "/api/auth/forgot-password", json={"email": "ada@example.com"}
    ).json()["reset_token"]

    assert client.post(
        "/api/auth/reset-password", json={"token": token, "password": "first-new-pass"}
    ).status_code == 200
    assert client.post(
        "/api/auth/reset-password", json={"token": token, "password": "second-new-pass"}
    ).status_code == 400


def test_requesting_a_new_link_retires_the_old_one(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    make_user("ada")
    first = client.post(
        "/api/auth/forgot-password", json={"email": "ada@example.com"}
    ).json()["reset_token"]
    second = client.post(
        "/api/auth/forgot-password", json={"email": "ada@example.com"}
    ).json()["reset_token"]

    assert client.post(
        "/api/auth/reset-password", json={"token": first, "password": "whatever-pass"}
    ).status_code == 400
    assert client.post(
        "/api/auth/reset-password", json={"token": second, "password": "whatever-pass"}
    ).status_code == 200


def test_expired_reset_token_is_rejected(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    make_user("ada")
    token = client.post(
        "/api/auth/forgot-password", json={"email": "ada@example.com"}
    ).json()["reset_token"]

    # Age the record rather than waiting out the TTL.
    session = next(client.app.dependency_overrides[get_db]())
    try:
        record = session.query(PasswordReset).one()
        from datetime import timedelta

        from app.models import utcnow

        record.expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
    finally:
        session.close()

    assert client.post(
        "/api/auth/reset-password", json={"token": token, "password": "too-late-pass"}
    ).status_code == 400


def test_unknown_reset_token_is_rejected(client):
    response = client.post(
        "/api/auth/reset-password",
        json={"token": "x" * 40, "password": "irrelevant-pass"},
    )
    assert response.status_code == 400


def test_reset_password_rejects_a_short_password(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    make_user("ada")
    token = client.post(
        "/api/auth/forgot-password", json={"email": "ada@example.com"}
    ).json()["reset_token"]

    assert client.post(
        "/api/auth/reset-password", json={"token": token, "password": "short"}
    ).status_code == 422
