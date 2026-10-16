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
    """A post needs text or an attachment; a bare empty body is a 400."""
    headers, _ = make_user("ada")
    assert client.post("/api/posts", headers=headers, json={"body": ""}).status_code == 400
    assert client.post("/api/posts", headers=headers, json={"body": "   "}).status_code == 400


def test_post_body_length_is_validated(client, make_user):
    headers, _ = make_user("ada")
    assert client.post("/api/posts", headers=headers, json={"body": "x" * 2001}).status_code == 422


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

    comments = client.get(f"/api/posts/{post['id']}/comments", headers=author_headers).json()
    assert len(comments) == 1

    detail = client.get(f"/api/posts/{post['id']}", headers=author_headers).json()
    assert detail["comment_count"] == 1

    comment_id = comments[0]["id"]
    assert client.delete(f"/api/comments/{comment_id}", headers=other_headers).status_code == 204
    assert client.get(f"/api/posts/{post['id']}/comments", headers=author_headers).json() == []


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
    assert client.get(f"/api/posts/{post['id']}", headers=headers).status_code == 404


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

    posts = client.get("/api/search/posts?q=compilers", headers=ada_headers).json()
    assert len(posts) == 1
    assert posts[0]["author"]["username"] == "grace"

    users = client.get("/api/search/users?q=gra", headers=ada_headers).json()
    assert [u["username"] for u in users] == ["grace"]

    combined = client.get("/api/search?q=fun", headers=ada_headers).json()
    assert len(combined["posts"]) == 1
    assert combined["users"] == []


def test_trending_tags(client, make_user):
    headers, _ = make_user("ada")
    client.post("/api/posts", headers=headers, json={"body": "one #python #code"})
    client.post("/api/posts", headers=headers, json={"body": "two #python"})

    tags = client.get("/api/trending/tags", headers=headers).json()
    assert tags[0] == {"name": "python", "uses": 2}


def test_repeated_tags_are_shared_not_duplicated(client, make_user):
    """The same hashtag across posts must resolve to one row, not a unique-constraint error."""
    headers, _ = make_user("ada")
    for index in range(3):
        response = client.post("/api/posts", headers=headers, json={"body": f"post {index} #shared"})
        assert response.status_code == 201, response.text

    tags = client.get("/api/trending/tags", headers=headers).json()
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

    other = client.get("/api/users/ada", headers=ada_headers).json()
    assert other["is_following"] is False


def test_read_endpoints_require_authentication(client, make_user):
    """Every read route is closed to anonymous callers, matching the gated UI."""
    headers, _ = make_user("ada")
    post = client.post("/api/posts", headers=headers, json={"body": "hello #tag"}).json()

    reads = [
        "/api/posts",
        "/api/feed",
        f"/api/posts/{post['id']}",
        f"/api/posts/{post['id']}/comments",
        "/api/trending/tags",
        "/api/trending/people",
        "/api/search/posts?q=hello",
        "/api/search/users?q=ada",
        "/api/search?q=hello",
        "/api/users/suggested",
        "/api/users/stats",
        "/api/users/ada",
        "/api/users/ada/posts",
        "/api/users/ada/likes",
        "/api/users/ada/followers",
        "/api/users/ada/following",
    ]
    for path in reads:
        assert client.get(path).status_code == 401, f"anonymous GET {path} was allowed"
        assert client.get(path, headers=headers).status_code == 200, f"authed GET {path} failed"


def test_feed_is_shuffled_per_seed_and_stable_within_one(client, make_user):
    """The home feed varies by seed but not between pages of the same seed.

    Two things must hold at once: a reload should not replay the same order (the
    "random instead of most recent" requirement), and paging through one session
    must not repeat or skip posts, which is what a fresh shuffle per request
    would do.
    """
    headers, _ = make_user("ada")
    for index in range(12):
        client.post("/api/posts", headers=headers, json={"body": f"post {index}"})

    def order(seed: str, page: int = 1, page_size: int = 6) -> list[int]:
        response = client.get(
            f"/api/feed?scope=all&page={page}&page_size={page_size}&seed={seed}", headers=headers
        )
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    first = order("seed-one")
    # Same seed, first page again: identical, so pagination can be trusted.
    assert order("seed-one") == first

    # A different seed produces a different arrangement (12 posts, so a
    # collision is vanishingly unlikely and would be a real bug in the shuffle).
    assert order("seed-two") != first

    # Page two of one seed shares nothing with page one: no repeats across pages.
    page_two = order("seed-one", page=2)
    assert not set(page_two) & set(first)

    # Together the two pages cover the whole pool exactly once.
    assert sorted(first + page_two) == sorted(order("seed-one", page=1, page_size=12))


def test_feed_prefers_recent_posts(client, make_user):
    """Relevance leans on recency: a fresh post beats an old one most draws.

    The order is random, so this is asserted statistically over many seeds
    rather than at a fixed index. With one post from now and one from a week
    ago, the fresh post has roughly eight times the weight, so it should lead
    the large majority of draws — while still losing some, which is what makes
    the feed feel random rather than sorted.
    """
    from datetime import timedelta

    from app.database import get_db
    from app.models import Post, utcnow

    headers, me = make_user("ada")

    override = client.app.dependency_overrides[get_db]
    session = next(override())
    try:
        session.add(Post(author_id=me["id"], body="old", created_at=utcnow() - timedelta(days=7)))
        session.commit()
        fresh = Post(author_id=me["id"], body="fresh", created_at=utcnow())
        session.add(fresh)
        session.commit()
        fresh_id = fresh.id
    finally:
        session.close()

    trials = 60
    wins = 0
    for index in range(trials):
        page = client.get(
            f"/api/feed?scope=all&page=1&page_size=1&seed=s{index}", headers=headers
        ).json()
        if page["items"][0]["id"] == fresh_id:
            wins += 1

    # Expected ~88%: high, but not 100%, because the ordering is random.
    assert wins > trials * 0.6, f"fresh post only led {wins}/{trials} draws"
    assert wins < trials, "fresh post led every draw, so nothing is being shuffled"


def test_feed_does_not_merely_reverse_chronologically(client, make_user):
    """The feed must be shuffled, not just newest-first under another name."""
    headers, _ = make_user("ada")
    for index in range(15):
        client.post("/api/posts", headers=headers, json={"body": f"p{index}"})

    orders = set()
    for index in range(8):
        page = client.get(
            f"/api/feed?scope=all&page=1&page_size=15&seed=s{index}", headers=headers
        ).json()
        orders.add(tuple(item["id"] for item in page["items"]))

    # Different seeds must produce different orders; a strict timestamp sort
    # would give one order every time.
    assert len(orders) > 1


def test_feed_defaults_to_following_scope(client, make_user):
    """The API default stays `following`; the client opts into Everyone."""
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    client.post("/api/posts", headers=grace_headers, json={"body": "from grace"})

    default = client.get("/api/feed", headers=ada_headers).json()
    assert default["total"] == 0

    everyone = client.get("/api/feed?scope=all", headers=ada_headers).json()
    assert everyone["total"] == 1


def test_like_is_idempotent(client, make_user):
    """Liking twice is one like, and the second tap is not an error."""
    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    post_id = client.post("/api/posts", headers=grace_headers, json={"body": "hi"}).json()["id"]

    first = client.post(f"/api/posts/{post_id}/like", headers=ada_headers)
    assert first.status_code == 200, first.text
    assert first.json()["liked_by_me"] is True
    assert first.json()["like_count"] == 1

    second = client.post(f"/api/posts/{post_id}/like", headers=ada_headers)
    assert second.status_code == 200, second.text
    assert second.json()["like_count"] == 1


def test_like_survives_a_lost_insert_race(client, make_user, monkeypatch):
    """The loser of a racing duplicate insert still gets a 200, not a 500.

    Two taps can both see "no like yet" and both try to insert. The unique
    constraint rejects the second, which is the correct outcome — so the handler
    must treat that IntegrityError as success rather than letting it escape as a
    500, which is what a burst of new users tapping the same post produced.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from app.models import Like

    ada_headers, _ = make_user("ada")
    grace_headers, _ = make_user("grace")
    post_id = client.post("/api/posts", headers=grace_headers, json={"body": "hi"}).json()["id"]

    original_flush = Session.flush

    def racy_flush(self, *args, **kwargs):
        # Simulate the constraint firing on this request's insert.
        if any(isinstance(obj, Like) for obj in self.new):
            raise IntegrityError("INSERT INTO likes", {}, Exception("UNIQUE constraint failed"))
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", racy_flush)

    response = client.post(f"/api/posts/{post_id}/like", headers=ada_headers)
    assert response.status_code == 200, response.text

