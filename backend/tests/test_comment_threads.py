"""Tests for threaded comment replies and comment likes."""


def make_post(client, headers, body="a post"):
    return client.post("/api/posts", headers=headers, json={"body": body}).json()


def comment(client, headers, post_id, body, parent_id=None):
    payload = {"body": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    return client.post(f"/api/posts/{post_id}/comments", headers=headers, json=payload)


def tree(client, headers, post_id):
    return client.get(f"/api/posts/{post_id}/comments", headers=headers).json()


def test_reply_is_nested_under_its_parent(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    bob, _ = make_user("bob")
    post = make_post(client, ada)

    top = comment(client, grace, post["id"], "top level").json()
    reply = comment(client, bob, post["id"], "a reply", parent_id=top["id"]).json()
    assert reply["parent_id"] == top["id"]

    comments = tree(client, ada, post["id"])
    assert [c["body"] for c in comments] == ["top level"]
    assert [r["body"] for r in comments[0]["replies"]] == ["a reply"]

    # A second top-level comment stays a sibling, not a reply.
    comment(client, bob, post["id"], "second top")
    comments = tree(client, ada, post["id"])
    assert [c["body"] for c in comments] == ["top level", "second top"]
    assert [r["body"] for r in comments[0]["replies"]] == ["a reply"]


def test_reply_to_a_reply_nests_one_level_deep(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)

    top = comment(client, grace, post["id"], "top").json()
    first = comment(client, ada, post["id"], "first", parent_id=top["id"]).json()
    second = comment(client, grace, post["id"], "second", parent_id=first["id"]).json()
    assert second["parent_id"] == first["id"]

    comments = tree(client, ada, post["id"])
    assert [c["body"] for c in comments] == ["top"]
    assert [r["body"] for r in comments[0]["replies"]] == ["first"]
    assert [r["body"] for r in comments[0]["replies"][0]["replies"]] == ["second"]


def test_reply_to_a_comment_on_another_post_is_rejected(client, make_user):
    ada, _ = make_user("ada")
    post_a = make_post(client, ada, "first post")
    post_b = make_post(client, ada, "second post")
    top = comment(client, ada, post_a["id"], "on the first post").json()

    response = comment(client, ada, post_b["id"], "wrong thread", parent_id=top["id"])
    assert response.status_code == 404


def test_reply_to_an_unknown_comment_is_rejected(client, make_user):
    ada, _ = make_user("ada")
    post = make_post(client, ada)
    assert comment(client, ada, post["id"], "orphan", parent_id=9999).status_code == 404


def test_reply_notifies_the_person_being_answered(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    bob, _ = make_user("bob")
    post = make_post(client, ada)

    top = comment(client, grace, post["id"], "top").json()
    comment(client, bob, post["id"], "answering grace", parent_id=top["id"])

    grace_notes = client.get("/api/notifications", headers=grace).json()
    assert [n["kind"] for n in grace_notes] == ["reply"]
    assert grace_notes[0]["actor"]["username"] == "bob"

    # The post author is told about the reply too, once.
    ada_notes = client.get("/api/notifications", headers=ada).json()
    assert sorted(n["kind"] for n in ada_notes) == ["comment", "reply"]


def test_replying_to_the_post_author_notifies_them_once(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)

    top = comment(client, ada, post["id"], "my own post").json()
    comment(client, grace, post["id"], "replying to the author", parent_id=top["id"])

    ada_notes = client.get("/api/notifications", headers=ada).json()
    assert [n["kind"] for n in ada_notes] == ["reply"]


def test_comment_like_toggles_and_is_per_viewer(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    bob, _ = make_user("bob")
    post = make_post(client, ada)
    top = comment(client, grace, post["id"], "top").json()

    liked = client.post(f"/api/comments/{top['id']}/like", headers=ada)
    assert liked.status_code == 200, liked.text
    assert liked.json()["like_count"] == 1
    assert liked.json()["liked_by_me"] is True

    # Liking twice does not double count.
    again = client.post(f"/api/comments/{top['id']}/like", headers=ada).json()
    assert again["like_count"] == 1

    # A different viewer sees the count but not the flag.
    seen = tree(client, bob, post["id"])
    assert seen[0]["like_count"] == 1
    assert seen[0]["liked_by_me"] is False

    # The author sees it as theirs, and the original liker can take it back.
    assert tree(client, ada, post["id"])[0]["liked_by_me"] is True
    unliked = client.delete(f"/api/comments/{top['id']}/like", headers=ada)
    assert unliked.status_code == 200
    assert unliked.json()["like_count"] == 0
    assert unliked.json()["liked_by_me"] is False


def test_comment_like_notifies_the_comment_author(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)
    top = comment(client, grace, post["id"], "top").json()

    client.post(f"/api/comments/{top['id']}/like", headers=ada)
    grace_notes = client.get("/api/notifications", headers=grace).json()
    assert [n["kind"] for n in grace_notes] == ["comment_like"]
    assert grace_notes[0]["actor"]["username"] == "ada"


def test_liking_your_own_comment_does_not_notify(client, make_user):
    ada, _ = make_user("ada")
    post = make_post(client, ada)
    top = comment(client, ada, post["id"], "mine").json()

    client.post(f"/api/comments/{top['id']}/like", headers=ada)
    assert client.get("/api/notifications", headers=ada).json() == []


def test_comment_like_requires_auth(client, make_user):
    ada, _ = make_user("ada")
    post = make_post(client, ada)
    top = comment(client, ada, post["id"], "top").json()
    assert client.post(f"/api/comments/{top['id']}/like").status_code == 401


def test_deleting_a_parent_removes_its_whole_subtree(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)

    top = comment(client, grace, post["id"], "top").json()
    first = comment(client, ada, post["id"], "first", parent_id=top["id"]).json()
    comment(client, grace, post["id"], "second", parent_id=first["id"])
    survivor = comment(client, grace, post["id"], "unrelated").json()

    assert client.delete(f"/api/comments/{top['id']}", headers=ada).status_code == 204

    remaining = tree(client, ada, post["id"])
    assert [c["body"] for c in remaining] == ["unrelated"]
    assert remaining[0]["id"] == survivor["id"]


def test_deleting_a_reply_leaves_its_parent(client, make_user):
    ada, _ = make_user("ada")
    grace, _ = make_user("grace")
    post = make_post(client, ada)

    top = comment(client, ada, post["id"], "top").json()
    reply = comment(client, grace, post["id"], "a reply", parent_id=top["id"]).json()

    assert client.delete(f"/api/comments/{reply['id']}", headers=grace).status_code == 204
    remaining = tree(client, ada, post["id"])
    assert [c["body"] for c in remaining] == ["top"]
    assert remaining[0]["replies"] == []


def test_comments_require_authentication(client):
    assert client.get("/api/posts/1/comments").status_code == 401
