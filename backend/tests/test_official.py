"""The official account, compulsory following, and announcement broadcasts."""

import pytest
from sqlalchemy import func, select

from app import official
from app.config import settings
from app.database import get_db
from app.models import Follow, Notification, User


def _session(client):
    override = client.app.dependency_overrides[get_db]
    return next(override())


@pytest.fixture
def official_account(client, make_user):
    """An account named like the configured official one, plus its followers.

    ``make_user`` registers through the API, so the account itself gets the
    compulsory follow the moment it is created — the same rule every user gets.
    """
    headers, me = make_user(settings.official_account_username)
    return headers, me


def test_registration_follows_the_official_account(client, official_account, make_user):
    """A brand-new account follows the official one without being asked."""
    _official_headers, official_user = official_account
    headers, me = make_user("ada")

    following = client.get("/api/users/ada/following", headers=headers).json()
    assert [u["username"] for u in following] == [official_user["username"]]


def test_registration_does_not_follow_when_the_rule_is_off(
    client, official_account, make_user, monkeypatch
):
    """The flag turns the rule off, so the follow is not created."""
    monkeypatch.setattr(settings, "official_follow_required", False)
    headers, _me = make_user("grace")

    assert client.get("/api/users/grace/following", headers=headers).json() == []


def test_registration_survives_a_missing_official_account(client, make_user, monkeypatch):
    """A database with no official account must still accept sign-ups."""
    monkeypatch.setattr(settings, "official_account_username", "nobody-here")
    headers, me = make_user("linus")

    assert me["username"] == "linus"
    assert client.get("/api/users/linus/following", headers=headers).json() == []


def test_the_official_account_cannot_be_unfollowed(client, official_account, make_user):
    """Removing the compulsory follow is refused rather than silently undone."""
    _official_headers, official_user = official_account
    headers, _me = make_user("ada")

    response = client.delete(
        f"/api/users/{official_user['username']}/follow", headers=headers
    )

    assert response.status_code == 400
    assert "cannot be unfollowed" in response.json()["detail"]
    following = client.get("/api/users/ada/following", headers=headers).json()
    assert [u["username"] for u in following] == [official_user["username"]]


def test_backfill_covers_accounts_created_before_the_rule(client, official_account, make_user):
    """Existing accounts are given the follow at startup, and only once."""
    _official_headers, official_user = official_account
    make_user("ada")
    make_user("grace")

    db = _session(client)
    try:
        # Simulate accounts that predate the rule by removing their follows.
        db.execute(Follow.__table__.delete())
        db.commit()

        assert official.backfill_official_follows(db) == 2
        # Idempotent: a second boot adds nothing.
        assert official.backfill_official_follows(db) == 0
    finally:
        db.close()

    rows = None
    db = _session(client)
    try:
        rows = db.scalar(
            select(func.count()).select_from(Follow).where(
                Follow.following_id == official_user["id"]
            )
        )
    finally:
        db.close()
    assert rows == 2


def test_backfill_leaves_the_official_account_alone(client, official_account):
    """The official account does not follow itself."""
    _official_headers, official_user = official_account

    db = _session(client)
    try:
        assert official.backfill_official_follows(db) == 0
        self_follow = db.scalar(
            select(Follow).where(
                Follow.follower_id == official_user["id"],
                Follow.following_id == official_user["id"],
            )
        )
        assert self_follow is None
    finally:
        db.close()


def test_broadcast_notifies_every_account(client, make_user, make_admin, official_account):
    """An announcement reaches accounts that do not follow the author."""
    admin_headers, admin = make_admin("root")
    official_headers, official_user = official_account
    _ada_headers, ada = make_user("ada")
    _grace_headers, grace = make_user("grace")

    post = client.post(
        "/api/posts",
        headers=official_headers,
        json={"body": "Opens in a blink. Everything is faster now."},
    ).json()

    response = client.post(
        "/api/admin/announcements",
        headers=admin_headers,
        json={"post_id": post["id"]},
    )

    assert response.status_code == 200, response.text
    result = response.json()

    # Every active account except the author, including accounts that do not
    # follow the author -- which is the whole point.
    db = _session(client)
    try:
        expected = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.id != official_user["id"], User.is_banned.is_(False))
        )
    finally:
        db.close()
    assert result["notified"] == expected

    # A non-follower of the author received it.
    for account in (ada, grace):
        headers, _me = _headers_for(client, account["username"])
        kinds = [n["kind"] for n in client.get("/api/notifications", headers=headers).json()]
        assert "announcement" in kinds, account["username"]


def test_broadcast_skips_banned_accounts(client, make_user, make_admin, official_account):
    """A banned account is not notified."""
    admin_headers, _admin = make_admin("root")
    official_headers, _official = official_account
    make_user("ada")
    banned_headers, banned = make_user("spammer")

    client.post(
        f"/api/admin/users/{banned['id']}/ban",
        headers=admin_headers,
        json={"banned": True, "reason": "spam"},
    )

    post = client.post(
        "/api/posts", headers=official_headers, json={"body": "An announcement."}
    ).json()
    result = client.post(
        "/api/admin/announcements", headers=admin_headers, json={"post_id": post["id"]}
    ).json()

    db = _session(client)
    try:
        active = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.id != _official["id"], User.is_banned.is_(False))
        )
    finally:
        db.close()
    assert result["notified"] == active

    # The banned account's token no longer works, so check the rows directly.
    # It may still hold the "post" notification the compulsory follow produced
    # when the announcement was published; what must not exist is the broadcast.
    db = _session(client)
    try:
        rows = db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == banned["id"],
                Notification.kind == "announcement",
            )
        )
    finally:
        db.close()
    assert rows == 0


def test_broadcast_rejects_a_missing_post(client, make_admin):
    admin_headers, _admin = make_admin("root")

    response = client.post(
        "/api/admin/announcements", headers=admin_headers, json={"post_id": 999999}
    )

    assert response.status_code == 404


def test_broadcast_requires_an_admin(client, make_user):
    headers, _me = make_user("ada")

    response = client.post(
        "/api/admin/announcements", headers=headers, json={"post_id": 1}
    )

    assert response.status_code == 403


def test_announcement_reads_as_platform_news(client, make_admin, official_account):
    """The sentence names Pulse, not the person who happened to post it."""
    from app.routers.notifications import notification_text

    assert (
        notification_text({"notification": {"kind": "announcement"}, "actor": {"display_name": "Pulse"}})
        == "Pulse posted an announcement"
    )
    assert (
        notification_text({"notification": {"kind": "announcement"}, "actor": None})
        == "Pulse posted an announcement"
    )


def _headers_for(client, username):
    """Log an existing account back in, for tests that need its token twice."""
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return headers, client.get("/api/auth/me", headers=headers).json()
