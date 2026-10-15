"""Tests for the follow graph, notifications and stats."""


def test_follow_and_unfollow(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, grace = make_user("grace")

    followed = client.post("/api/users/grace/follow", headers=ada_headers)
    assert followed.status_code == 201
    assert followed.json()["is_following"] is True
    assert followed.json()["follower_count"] == 1

    # Following twice is idempotent.
    client.post("/api/users/grace/follow", headers=ada_headers)
    assert client.get("/api/users/grace", headers=ada_headers).json()["follower_count"] == 1

    profile = client.get("/api/users/ada", headers=ada_headers).json()
    assert profile["following_count"] == 1

    followers = client.get("/api/users/grace/followers", headers=ada_headers).json()
    assert [u["username"] for u in followers] == ["ada"]
    assert client.get("/api/users/ada/following", headers=ada_headers).json()[0]["username"] == "grace"

    unfollowed = client.delete("/api/users/grace/follow", headers=ada_headers)
    assert unfollowed.json()["follower_count"] == 0


def test_cannot_follow_self(client, make_user):
    headers, _ = make_user("ada")
    assert client.post("/api/users/ada/follow", headers=headers).status_code == 400


def test_follow_creates_notification_and_read_state(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")

    client.post("/api/users/grace/follow", headers=ada_headers)

    notifications = client.get("/api/notifications", headers=grace_headers).json()
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "follow"
    assert notifications[0]["read"] is False

    assert client.get("/api/notifications/unread-count", headers=grace_headers).json() == {"count": 1}

    read = client.post(f"/api/notifications/{notifications[0]['id']}/read", headers=grace_headers)
    assert read.status_code == 200
    assert read.json()["read"] is True
    assert client.get("/api/notifications/unread-count", headers=grace_headers).json() == {"count": 0}

    assert client.get("/api/notifications", headers=grace_headers, params={"unread_only": True}).json() == []


def test_cannot_read_another_users_notification(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    linus_headers, _ = make_user("linus")
    client.post("/api/users/grace/follow", headers=ada_headers)

    notification = client.get("/api/notifications", headers=grace_headers).json()[0]
    response = client.post(f"/api/notifications/{notification['id']}/read", headers=linus_headers)
    assert response.status_code == 404


def test_mark_all_read(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    linus_headers, _ = make_user("linus")
    client.post("/api/users/grace/follow", headers=ada_headers)
    client.post("/api/users/grace/follow", headers=linus_headers)

    assert client.get("/api/notifications/unread-count", headers=grace_headers).json()["count"] == 2
    result = client.post("/api/notifications/read-all", headers=grace_headers)
    assert result.json()["updated"] == 2
    assert client.get("/api/notifications/unread-count", headers=grace_headers).json()["count"] == 0


def test_suggested_users_excludes_followed(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, grace = make_user("grace")
    make_user("linus")

    client.post("/api/users/grace/follow", headers=ada_headers)

    suggested = client.get("/api/users/suggested", headers=ada_headers).json()
    usernames = [item["user"]["username"] for item in suggested]
    assert "grace" not in usernames
    assert "ada" not in usernames
    assert "linus" in usernames


def test_platform_stats(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    post = client.post("/api/posts", headers=ada_headers, json={"body": "hello"}).json()
    client.post(f"/api/posts/{post['id']}/like", headers=grace_headers)
    client.post(f"/api/posts/{post['id']}/comments", headers=grace_headers, json={"body": "hi"})

    stats = client.get("/api/users/stats", headers=ada_headers).json()
    assert stats == {"users": 2, "posts": 1, "likes": 1, "comments": 1}


def test_user_likes_tab(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    post = client.post("/api/posts", headers=ada_headers, json={"body": "hello"}).json()
    client.post(f"/api/posts/{post['id']}/like", headers=grace_headers)

    liked = client.get("/api/users/grace/likes", headers=grace_headers).json()
    assert liked["total"] == 1
    assert liked["items"][0]["body"] == "hello"


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"
