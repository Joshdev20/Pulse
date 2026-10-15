"""Tests for reposts (sharing another account's post)."""


def make_post(client, headers, body: str = "hello world"):
    response = client.post("/api/posts", headers=headers, json={"body": body})
    assert response.status_code == 201, response.text
    return response.json()


def test_repost_requires_authentication(client, make_user):
    headers, _ = make_user("ada")
    post = make_post(client, headers)
    assert client.post(f"/api/posts/{post['id']}/repost").status_code == 401


def test_repost_returns_the_original_with_updated_state(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)

    response = client.post(f"/api/posts/{post['id']}/repost", headers=grace)
    assert response.status_code == 201, response.text
    body = response.json()

    # The original is returned, so the caller can update it in place.
    assert body["id"] == post["id"]
    assert body["repost_count"] == 1
    assert body["reposted_by_me"] is True
    assert body["repost_of"] is None


def test_repost_row_appears_in_the_feed_with_attribution(client, make_user):
    ada, _ = make_user("ada")
    grace, grace_user = make_user("grace")
    post = make_post(client, ada, "worth sharing")

    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    feed = client.get("/api/feed", headers=grace, params={"scope": "all"}).json()
    reposts = [item for item in feed["items"] if item["repost_of"] is not None]
    assert len(reposts) == 1

    row = reposts[0]
    # The envelope describes who shared it...
    assert row["author"]["username"] == "grace"
    assert row["id"] != post["id"]
    assert row["body"] == ""
    assert row["media"] == []
    # ...and the nested original carries the content.
    assert row["repost_of"]["id"] == post["id"]
    assert row["repost_of"]["author"]["username"] == "ada"
    assert row["repost_of"]["body"] == "worth sharing"
    # Nested one level only, so serialization terminates.
    assert row["repost_of"]["repost_of"] is None


def test_repost_shows_in_followers_feed(client, make_user):
    """A share reaches the sharer's followers, not just the Everyone feed."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    follower, _ = make_user("linus")
    post = make_post(client, ada)

    client.post("/api/users/grace/follow", headers=follower)
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    feed = client.get("/api/feed", headers=follower, params={"scope": "following"}).json()
    assert any(item["repost_of"] for item in feed["items"])


def test_repost_counts_agree_between_original_and_share(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)

    # A like on the original must show on the repost row's nested original too.
    client.post(f"/api/posts/{post['id']}/like", headers=grace)
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    feed = client.get("/api/feed", headers=grace, params={"scope": "all"}).json()
    row = next(item for item in feed["items"] if item["repost_of"])
    assert row["repost_of"]["like_count"] == 1
    assert row["repost_of"]["liked_by_me"] is True
    assert row["repost_of"]["repost_count"] == 1


def test_reposting_twice_is_idempotent(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)

    client.post(f"/api/posts/{post['id']}/repost", headers=grace)
    second = client.post(f"/api/posts/{post['id']}/repost", headers=grace)
    assert second.json()["repost_count"] == 1

    feed = client.get("/api/feed", headers=grace, params={"scope": "all"}).json()
    assert len([item for item in feed["items"] if item["repost_of"]]) == 1


def test_unrepost_removes_the_share(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    response = client.delete(f"/api/posts/{post['id']}/repost", headers=grace)
    assert response.status_code == 200
    assert response.json()["repost_count"] == 0
    assert response.json()["reposted_by_me"] is False

    feed = client.get("/api/feed", headers=grace, params={"scope": "all"}).json()
    assert not [item for item in feed["items"] if item["repost_of"]]


def test_unrepost_when_not_shared_is_idempotent(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)
    response = client.delete(f"/api/posts/{post['id']}/repost", headers=grace)
    assert response.status_code == 200
    assert response.json()["repost_count"] == 0


def test_cannot_repost_your_own_post(client, make_user):
    ada, _ = make_user("ada")
    post = make_post(client, ada)
    response = client.post(f"/api/posts/{post['id']}/repost", headers=ada)
    assert response.status_code == 400


def test_reposting_a_repost_points_at_the_original(client, make_user):
    """Sharing a share must not build a chain."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    linus, _ = make_user("linus")
    post = make_post(client, ada)

    client.post(f"/api/posts/{post['id']}/repost", headers=grace)
    feed = client.get("/api/feed", headers=linus, params={"scope": "all"}).json()
    share_id = next(item["id"] for item in feed["items"] if item["repost_of"])

    response = client.post(f"/api/posts/{share_id}/repost", headers=linus)
    assert response.status_code == 201
    # Both shares point at the original, so the count is 2 and the original is
    # still one level deep rather than a chain.
    assert response.json()["id"] == post["id"]
    assert response.json()["repost_count"] == 2

    feed = client.get("/api/feed", headers=linus, params={"scope": "all"}).json()
    for item in feed["items"]:
        if item["repost_of"]:
            assert item["repost_of"]["id"] == post["id"]
            assert item["repost_of"]["repost_of"] is None


def test_repost_notifies_the_original_author(client, make_user):
    ada, _ = make_user("ada")
    grace, grace_user = make_user("grace")
    post = make_post(client, ada)

    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    notifications = client.get("/api/notifications", headers=ada).json()
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "repost"
    assert notifications[0]["actor"]["username"] == "grace"
    assert notifications[0]["post_id"] == post["id"]


def test_repost_unknown_post_is_404(client, make_user):
    headers, _ = make_user("ada")
    assert client.post("/api/posts/9999/repost", headers=headers).status_code == 404
    assert client.delete("/api/posts/9999/repost", headers=headers).status_code == 404


def test_deleting_the_original_removes_its_reposts(client, make_user):
    """A share cannot outlive the post it points at."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    assert client.delete(f"/api/posts/{post['id']}", headers=ada).status_code == 204

    feed = client.get("/api/feed", headers=grace, params={"scope": "all"}).json()
    assert feed["items"] == []
    assert feed["total"] == 0


def test_deleting_a_repost_leaves_the_original(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada, "still here")
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    feed = client.get("/api/feed", headers=grace, params={"scope": "all"}).json()
    share_id = next(item["id"] for item in feed["items"] if item["repost_of"])

    # The sharer may delete their own share.
    assert client.delete(f"/api/posts/{share_id}", headers=grace).status_code == 204

    remaining = client.get(f"/api/posts/{post['id']}", headers=ada).json()
    assert remaining["body"] == "still here"
    assert remaining["repost_count"] == 0


def test_cannot_delete_someone_elses_repost(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    linus, _ = make_user("linus")
    post = make_post(client, ada)
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    feed = client.get("/api/feed", headers=linus, params={"scope": "all"}).json()
    share_id = next(item["id"] for item in feed["items"] if item["repost_of"])
    assert client.delete(f"/api/posts/{share_id}", headers=linus).status_code == 403


def test_repost_has_no_text_to_edit(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    feed = client.get("/api/feed", headers=grace, params={"scope": "all"}).json()
    share_id = next(item["id"] for item in feed["items"] if item["repost_of"])
    response = client.patch(f"/api/posts/{share_id}", headers=grace, json={"body": "rewritten"})
    assert response.status_code == 400


def test_list_reposts_returns_sharers(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    linus, _ = make_user("linus")
    post = make_post(client, ada)
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)
    client.post(f"/api/posts/{post['id']}/repost", headers=linus)

    sharers = client.get(f"/api/posts/{post['id']}/reposts", headers=ada).json()
    assert {user["username"] for user in sharers} == {"grace", "linus"}


def test_repost_count_is_zero_for_a_plain_post(client, make_user):
    headers, _ = make_user("ada")
    post = make_post(client, headers)
    assert post["repost_count"] == 0
    assert post["reposted_by_me"] is False
    assert post["repost_of"] is None


def test_repost_does_not_break_user_profile_pages(client, make_user):
    """A share must not be attributed to the original author's post list."""
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada, "original")
    client.post(f"/api/posts/{post['id']}/repost", headers=grace)

    ada_posts = client.get("/api/users/ada/posts", headers=ada).json()
    assert ada_posts["total"] == 1
    assert all(item["repost_of"] is None for item in ada_posts["items"])

    # The share belongs to the sharer's own posts, rendered as a repost.
    grace_posts = client.get("/api/users/grace/posts", headers=grace).json()
    assert grace_posts["total"] == 1
    assert grace_posts["items"][0]["repost_of"]["body"] == "original"
