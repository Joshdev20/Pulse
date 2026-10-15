"""Admin endpoints: authorization, user controls, moderation and analytics."""


def _post(client, headers, body: str) -> dict:
    response = client.post("/api/posts", json={"body": body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------


def test_admin_routes_reject_anonymous(client):
    for path in ("/api/admin/stats", "/api/admin/users", "/api/admin/posts", "/api/admin/comments"):
        assert client.get(path).status_code == 401, path


def test_admin_routes_reject_regular_user(client, make_user):
    headers, _ = make_user("regular")
    for path in ("/api/admin/stats", "/api/admin/users", "/api/admin/posts", "/api/admin/comments"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, path
        assert "Administrator" in response.json()["detail"]


def test_regular_user_cannot_ban_anyone(client, make_user):
    headers, _ = make_user("regular")
    victim, victim_user = make_user("victim")
    response = client.post(f"/api/admin/users/{victim_user['id']}/ban", json={"banned": True}, headers=headers)
    assert response.status_code == 403


def test_admin_flag_is_exposed_on_me(client, make_user, make_admin):
    _, regular = make_user("regular")
    _, admin = make_admin("root")
    assert regular["is_admin"] is False
    assert admin["is_admin"] is True


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------


def test_stats_counts_users_posts_and_engagement(client, make_admin, make_user):
    admin_headers, admin = make_admin("root")
    author_headers, author = make_user("author")
    other_headers, other = make_user("other")

    post = _post(client, author_headers, "first post")
    _post(client, author_headers, "second post")
    client.post(f"/api/posts/{post['id']}/like", headers=other_headers)
    client.post(f"/api/posts/{post['id']}/comments", json={"body": "nice"}, headers=other_headers)

    stats = client.get("/api/admin/stats", headers=admin_headers).json()

    assert stats["total_users"] == 3
    assert stats["active_users"] == 3
    assert stats["banned_users"] == 0
    assert stats["admin_users"] == 1
    assert stats["total_posts"] == 2
    assert stats["total_comments"] == 1
    assert stats["total_likes"] == 1
    assert stats["new_users_7d"] == 3
    assert stats["posts_7d"] == 2
    # (likes + comments) / posts
    assert stats["engagement_rate"] == 1.0
    assert stats["avg_posts_per_user"] == round(2 / 3, 2)


def test_stats_top_posters_and_tags(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    author_headers, author = make_user("author")
    _post(client, author_headers, "hello #python")
    _post(client, author_headers, "more #python")

    stats = client.get("/api/admin/stats", headers=admin_headers).json()

    assert stats["top_posters"][0] == {"label": "author", "value": 2}
    assert stats["top_tags"][0] == {"label": "python", "value": 2}


def test_stats_on_empty_platform_does_not_divide_by_zero(client, make_admin):
    admin_headers, _ = make_admin("root")
    stats = client.get("/api/admin/stats", headers=admin_headers).json()
    assert stats["total_posts"] == 0
    assert stats["engagement_rate"] == 0.0
    assert stats["avg_posts_per_user"] == 0.0


# --------------------------------------------------------------------------
# User controls
# --------------------------------------------------------------------------


def test_list_users_reports_counts_and_paginates(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    author_headers, author = make_user("author")
    _post(client, author_headers, "a post")

    page = client.get("/api/admin/users?page_size=2", headers=admin_headers).json()

    assert page["total"] == 2
    assert page["page_size"] == 2
    assert page["has_more"] is False
    entry = next(item for item in page["items"] if item["username"] == "author")
    assert entry["post_count"] == 1
    assert entry["email"] == "author@example.com"
    assert entry["is_banned"] is False


def test_list_users_search_and_status_filter(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    make_user("alice")
    make_user("bob")

    found = client.get("/api/admin/users?q=ali", headers=admin_headers).json()
    assert [item["username"] for item in found["items"]] == ["alice"]

    by_email = client.get("/api/admin/users?q=bob@example.com", headers=admin_headers).json()
    assert [item["username"] for item in by_email["items"]] == ["bob"]

    admins = client.get("/api/admin/users?status=admin", headers=admin_headers).json()
    assert [item["username"] for item in admins["items"]] == ["root"]


def test_ban_and_unban_user(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    victim_headers, victim = make_user("victim")

    banned = client.post(
        f"/api/admin/users/{victim['id']}/ban",
        json={"banned": True, "reason": "Spam"},
        headers=admin_headers,
    )
    assert banned.status_code == 200, banned.text
    assert banned.json()["is_banned"] is True
    assert banned.json()["banned_reason"] == "Spam"

    # An existing token must stop working immediately, not at expiry.
    assert client.get("/api/auth/me", headers=victim_headers).status_code == 403
    assert client.post("/api/posts", json={"body": "sneaky"}, headers=victim_headers).status_code == 403
    # And the credentials must no longer mint a token.
    assert client.post("/api/auth/login", json={"username": "victim", "password": "password123"}).status_code == 403

    lifted = client.post(
        f"/api/admin/users/{victim['id']}/ban", json={"banned": False}, headers=admin_headers
    )
    assert lifted.status_code == 200
    assert lifted.json()["is_banned"] is False
    assert lifted.json()["banned_reason"] == ""
    assert client.get("/api/auth/me", headers=victim_headers).status_code == 200


def test_admin_cannot_ban_self(client, make_admin):
    headers, admin = make_admin("root")
    response = client.post(f"/api/admin/users/{admin['id']}/ban", json={"banned": True}, headers=headers)
    assert response.status_code == 400
    assert "own account" in response.json()["detail"]


def test_sole_admin_cannot_demote_ban_or_delete_themselves(client, make_admin):
    """The only administrator cannot leave the platform without a moderator.

    Self-demotion reaches the last-admin guard; banning and deleting oneself are
    refused a step earlier, for the same reason.
    """
    headers, admin = make_admin("root")

    demote = client.post(
        f"/api/admin/users/{admin['id']}/admin", json={"is_admin": False}, headers=headers
    )
    assert demote.status_code == 400
    assert "only administrator" in demote.json()["detail"]

    ban = client.post(f"/api/admin/users/{admin['id']}/ban", json={"banned": True}, headers=headers)
    assert ban.status_code == 400
    assert "own account" in ban.json()["detail"]

    delete = client.delete(f"/api/admin/users/{admin['id']}", headers=headers)
    assert delete.status_code == 400
    assert "own account" in delete.json()["detail"]

    # Nothing changed.
    still = client.get(f"/api/admin/users/{admin['id']}", headers=headers).json()
    assert still["is_admin"] is True
    assert still["is_banned"] is False


def test_sole_admin_is_protected_from_a_demoted_peer(client, make_admin):
    """Once demoted, a former admin cannot delete the remaining one."""
    headers, admin = make_admin("root")
    peer_headers, peer = make_admin("peer")

    # Two admins, so `peer` may demote `root`.
    assert (
        client.post(
            f"/api/admin/users/{admin['id']}/admin", json={"is_admin": False}, headers=peer_headers
        ).status_code
        == 200
    )
    # `root` is a regular user now: the admin routes are closed to them.
    assert client.delete(f"/api/admin/users/{peer['id']}", headers=headers).status_code == 403
    # And `peer`, the sole admin, still cannot remove themselves.
    assert (
        client.post(
            f"/api/admin/users/{peer['id']}/admin", json={"is_admin": False}, headers=peer_headers
        ).status_code
        == 400
    )


def test_promote_and_demote_second_admin(client, make_admin, make_user):
    headers, admin = make_admin("root")
    _, other = make_user("other")

    promoted = client.post(
        f"/api/admin/users/{other['id']}/admin", json={"is_admin": True}, headers=headers
    )
    assert promoted.status_code == 200
    assert promoted.json()["is_admin"] is True

    # With two admins, the first may now step down.
    demoted = client.post(
        f"/api/admin/users/{admin['id']}/admin", json={"is_admin": False}, headers=headers
    )
    assert demoted.status_code == 200
    assert demoted.json()["is_admin"] is False


def test_delete_user_removes_their_content(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    author_headers, author = make_user("author")
    post = _post(client, author_headers, "to be removed")

    assert client.delete(f"/api/admin/users/{author['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/posts/{post['id']}", headers=admin_headers).status_code == 404
    assert client.get(f"/api/admin/users/{author['id']}", headers=admin_headers).status_code == 404

    page = client.get("/api/admin/users", headers=admin_headers).json()
    assert [item["username"] for item in page["items"]] == ["root"]


def test_missing_user_returns_404(client, make_admin):
    headers, _ = make_admin("root")
    assert client.get("/api/admin/users/99999", headers=headers).status_code == 404
    assert client.post("/api/admin/users/99999/ban", json={"banned": True}, headers=headers).status_code == 404


# --------------------------------------------------------------------------
# Content moderation
# --------------------------------------------------------------------------


def test_list_posts_search_and_filter_by_author(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    alice_headers, _ = make_user("alice")
    bob_headers, _ = make_user("bob")
    _post(client, alice_headers, "apples are good")
    _post(client, bob_headers, "bananas are better")

    all_posts = client.get("/api/admin/posts", headers=admin_headers).json()
    assert all_posts["total"] == 2

    searched = client.get("/api/admin/posts?q=banana", headers=admin_headers).json()
    assert [item["author_username"] for item in searched["items"]] == ["bob"]

    filtered = client.get("/api/admin/posts?author=alice", headers=admin_headers).json()
    assert [item["body"] for item in filtered["items"]] == ["apples are good"]


def test_admin_can_delete_any_post(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    author_headers, _ = make_user("author")
    post = _post(client, author_headers, "remove me")

    assert client.delete(f"/api/admin/posts/{post['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/posts/{post['id']}", headers=admin_headers).status_code == 404
    assert client.delete(f"/api/admin/posts/{post['id']}", headers=admin_headers).status_code == 404


def test_admin_can_delete_a_comment(client, make_admin, make_user):
    admin_headers, _ = make_admin("root")
    author_headers, _ = make_user("author")
    commenter_headers, _ = make_user("commenter")
    post = _post(client, author_headers, "a post")
    comment = client.post(
        f"/api/posts/{post['id']}/comments", json={"body": "rude"}, headers=commenter_headers
    ).json()

    listed = client.get("/api/admin/comments", headers=admin_headers).json()
    assert listed["total"] == 1
    assert listed["items"][0]["author_username"] == "commenter"
    assert listed["items"][0]["post_id"] == post["id"]

    assert client.delete(f"/api/admin/comments/{comment['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/posts/{post['id']}/comments", headers=admin_headers).json() == []
    assert client.delete(f"/api/admin/comments/{comment['id']}", headers=admin_headers).status_code == 404


def test_moderation_listing_hides_nothing_but_marks_state(client, make_admin, make_user):
    """A banned author's posts stay visible to a moderator, flagged by state."""
    admin_headers, _ = make_admin("root")
    author_headers, author = make_user("author")
    _post(client, author_headers, "authored before the ban")
    client.post(f"/api/admin/users/{author['id']}/ban", json={"banned": True}, headers=admin_headers)

    posts = client.get("/api/admin/posts", headers=admin_headers).json()
    assert posts["total"] == 1
    users = client.get("/api/admin/users?status=banned", headers=admin_headers).json()
    assert [item["username"] for item in users["items"]] == ["author"]
