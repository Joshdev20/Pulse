"""Tests for posts, likes, comments, feed and search."""


def test_create_post_extracts_tags(client, make_user):
    headers, _ = make_user("ada")
    response = client.post(
        "/api/posts",
        headers=headers,
        json={"body": "Exploring #algorithms and #math today."},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["like_count"] == 0
    assert body["comment_count"] == 0
    assert body["liked_by_me"] is False
    assert sorted(tag["name"] for tag in body["tags"]) == ["algorithms", "math"]
    assert body["author"]["username"] == "ada"


def test_post_requires_authentication(client):
    assert client.post("/api/posts", json={"body": "hello"}).status_code == 401


def test_empty_post_is_rejected(client, make_user):
    headers, _ = make_user("ada")
    assert client.post("/api/posts", headers=headers, json={"body": ""}).status_code == 422


def test_like_and_unlike(client, make_user):
    author_headers, _ = make_user("ada")
    other_headers, _ = make_user("grace")
    post = client.post("/api/posts", headers=author_headers, json={"body": "hello world"}).json()

    liked = client.post(f"/api/posts/{post['id']}/like", headers=other_headers)
    assert liked.status_code == 200
    assert liked.json()["like_count"] == 1
    assert liked.json()["liked_by_me"] is True

    # Liking twice must not double count.
    again = client.post(f"/api/posts/{post['id']}/like", headers=other_headers)
    assert again.json()["like_count"] == 1

    unliked = client.delete(f"/api/posts/{post['id']}/like", headers=other_headers)
    assert unliked.json()["like_count"] == 0
    assert unliked.json()["liked_by_me"] is False


def test_like_creates_notification(client, make_user):
    author_headers, author = make_user("ada")
    other_headers, other = make_user("grace")
    post = client.post("/api/posts", headers=author_headers, json={"body": "hello"}).json()
    client.post(f"/api/posts/{post['id']}/like", headers=other_headers)

    notifications = client.get("/api/notifications", headers=author_headers).json()
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "like"
    assert notifications[0]["actor"]["username"] == "grace"

    # Liking your own post should not notify you.
    client.post(f"/api/posts/{post['id']}/like", headers=author_headers)
    assert client.get("/api/notifications", headers=author_headers).json()[0]["kind"] == "like"
    assert len(client.get("/api/notifications", headers=author_headers).json()) == 1


def test_comments_flow(client, make_user):
    author_headers, _ = make_user("ada")
    other_headers, _ = make_user("grace")
    post = client.post("/api/posts", headers=author_headers, json={"body": "hello"}).json()

    created = client.post(
        f"/api/posts/{post['id']}/comments", headers=other_headers, json={"body": "Nice one"}
    )
    assert created.status_code == 201
    assert created.json()["author"]["username"] == "grace"

    comments = client.get(f"/api/posts/{post['id']}/comments").json()
    assert len(comments) == 1

    detail = client.get(f"/api/posts/{post['id']}").json()
    assert detail["comment_count"] == 1

    comment_id = comments[0]["id"]
    assert client.delete(f"/api/comments/{comment_id}", headers=other_headers).status_code == 204
    assert client.get(f"/api/posts/{post['id']}/comments").json() == []


def test_post_author_can_delete_comment(client, make_user):
    author_headers, _ = make_user("ada")
    other_headers, _ = make_user("grace")
    third_headers, _ = make_user("linus")
    post = client.post("/api/posts", headers=author_headers, json={"body": "hello"}).json()
    comment = client.post(
        f"/api/posts/{post['id']}/comments", headers=other_headers, json={"body": "hi"}
    ).json()

    # An unrelated user may not delete it.
    assert client.delete(f"/api/comments/{comment['id']}", headers=third_headers).status_code == 403
    # The post author may.
    assert client.delete(f"/api/comments/{comment['id']}", headers=author_headers).status_code == 204


def test_edit_and_delete_own_post(client, make_user):
    headers, _ = make_user("ada")
    other_headers, _ = make_user("grace")
    post = client.post("/api/posts", headers=headers, json={"body": "first draft #old"}).json()

    forbidden = client.patch(f"/api/posts/{post['id']}", headers=other_headers, json={"body": "no"})
    assert forbidden.status_code == 403

    updated = client.patch(
        f"/api/posts/{post['id']}", headers=headers, json={"body": "final draft #new"}
    ).json()
    assert updated["body"] == "final draft #new"
    assert updated["edited_at"] is not None
    assert [tag["name"] for tag in updated["tags"]] == ["new"]

    assert client.delete(f"/api/posts/{post['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/posts/{post['id']}").status_code == 404


def test_feed_scopes(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    linus_headers, _ = make_user("linus")

    client.post("/api/posts", headers=grace_headers, json={"body": "from grace"})
    client.post("/api/posts", headers=linus_headers, json={"body": "from linus"})
    client.post("/api/posts", headers=ada_headers, json={"body": "from ada"})

    # Following feed starts with only your own posts.
    following = client.get("/api/feed?scope=following", headers=ada_headers).json()
    assert [p["author"]["username"] for p in following["items"]] == ["ada"]

    client.post("/api/users/grace/follow", headers=ada_headers)
    following = client.get("/api/feed?scope=following", headers=ada_headers).json()
    assert sorted(p["author"]["username"] for p in following["items"]) == ["ada", "grace"]

    everyone = client.get("/api/feed?scope=all", headers=ada_headers).json()
    assert everyone["total"] == 3

    assert client.get("/api/feed").status_code == 401


def test_feed_pagination(client, make_user):
    headers, _ = make_user("ada")
    for index in range(5):
        client.post("/api/posts", headers=headers, json={"body": f"post {index}"})

    first = client.get("/api/posts?page=1&page_size=2", headers=headers).json()
    assert len(first["items"]) == 2
    assert first["total"] == 5
    assert first["has_more"] is True

    last = client.get("/api/posts?page=3&page_size=2", headers=headers).json()
    assert len(last["items"]) == 1
    assert last["has_more"] is False

    # Posts are returned newest first.
    bodies = [p["body"] for p in first["items"]]
    assert bodies == ["post 4", "post 3"]


def test_search(client, make_user):
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    client.post("/api/posts", headers=grace_headers, json={"body": "compilers are fun #cobol"})
    client.post("/api/posts", headers=ada_headers, json={"body": "analytical engines"})

    posts = client.get("/api/search/posts?q=compilers").json()
    assert len(posts) == 1
    assert posts[0]["author"]["username"] == "grace"

    users = client.get("/api/search/users?q=gra").json()
    assert [u["username"] for u in users] == ["grace"]

    combined = client.get("/api/search?q=fun").json()
    assert len(combined["posts"]) == 1
    assert combined["users"] == []


def test_trending_tags(client, make_user):
    headers, _ = make_user("ada")
    client.post("/api/posts", headers=headers, json={"body": "one #python #code"})
    client.post("/api/posts", headers=headers, json={"body": "two #python"})

    tags = client.get("/api/trending/tags").json()
    assert tags[0] == {"name": "python", "uses": 2}


def test_repeated_tags_are_shared_not_duplicated(client, make_user):
    """The same hashtag across posts must resolve to one row, not a unique-constraint error."""
    headers, _ = make_user("ada")
    for index in range(3):
        response = client.post("/api/posts", headers=headers, json={"body": f"post {index} #shared"})
        assert response.status_code == 201, response.text

    tags = client.get("/api/trending/tags").json()
    assert tags == [{"name": "shared", "uses": 3}]

    # Two new tags in one post must also not collide.
    response = client.post("/api/posts", headers=headers, json={"body": "#alpha and #beta"})
    assert response.status_code == 201
    assert sorted(tag["name"] for tag in response.json()["tags"]) == ["alpha", "beta"]


def test_profile_post_counts(client, make_user):
    ada_headers, _ = make_user("ada")
    client.post("/api/posts", headers=ada_headers, json={"body": "one"})
    client.post("/api/posts", headers=ada_headers, json={"body": "two"})

    profile = client.get("/api/users/ada", headers=ada_headers).json()
    assert profile["post_count"] == 2

    posts = client.get("/api/users/ada/posts", headers=ada_headers).json()
    assert posts["total"] == 2

    other = client.get("/api/users/ada").json()
    assert other["is_following"] is False
