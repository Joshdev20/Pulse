"""Reporting a post, and the moderation queue that follows it."""

from app.models import Post, PostReport


def _post(client, headers, body="Something worth flagging"):
    response = client.post("/api/posts", headers=headers, json={"body": body})
    assert response.status_code == 201, response.text
    return response.json()


def test_report_is_recorded(client, make_user, db_session):
    author_headers, _ = make_user("ada")
    reporter_headers, reporter = make_user("grace")
    post = _post(client, author_headers)

    response = client.post(
        f"/api/posts/{post['id']}/report",
        headers=reporter_headers,
        json={"reason": "spam", "details": "Selling something in every reply"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["post_id"] == post["id"]
    assert body["reason"] == "spam"
    assert body["status"] == "open"

    row = db_session.get(PostReport, body["id"])
    assert row is not None
    assert row.reporter_id == reporter["id"]
    assert row.details == "Selling something in every reply"


def test_reporting_twice_returns_the_first_complaint(client, make_user, db_session):
    author_headers, _ = make_user("ada")
    reporter_headers, _ = make_user("grace")
    post = _post(client, author_headers)

    first = client.post(
        f"/api/posts/{post['id']}/report", headers=reporter_headers, json={"reason": "spam"}
    )
    second = client.post(
        f"/api/posts/{post['id']}/report", headers=reporter_headers, json={"reason": "harassment"}
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["reason"] == "spam"
    assert db_session.query(PostReport).count() == 1


def test_unknown_reason_is_rejected(client, make_user):
    author_headers, _ = make_user("ada")
    reporter_headers, _ = make_user("grace")
    post = _post(client, author_headers)
    response = client.post(
        f"/api/posts/{post['id']}/report", headers=reporter_headers, json={"reason": "because"}
    )
    assert response.status_code == 422


def test_own_post_cannot_be_reported(client, make_user):
    author_headers, _ = make_user("ada")
    post = _post(client, author_headers)
    response = client.post(
        f"/api/posts/{post['id']}/report", headers=author_headers, json={"reason": "spam"}
    )
    assert response.status_code == 400


def test_reporting_a_repost_targets_the_original(client, make_user):
    author_headers, author = make_user("ada")
    sharer_headers, _ = make_user("grace")
    reporter_headers, _ = make_user("linus")
    post = _post(client, author_headers)

    repost = client.post(f"/api/posts/{post['id']}/repost", headers=sharer_headers).json()
    response = client.post(
        f"/api/posts/{repost['id']}/report", headers=reporter_headers, json={"reason": "misleading"}
    )
    assert response.status_code == 201
    # The complaint lands on the content a moderator can act on, not the wrapper.
    assert response.json()["post_id"] == post["id"]


def test_report_requires_a_session(client, make_user):
    author_headers, _ = make_user("ada")
    post = _post(client, author_headers)
    assert client.post(f"/api/posts/{post['id']}/report", json={"reason": "spam"}).status_code == 401


def test_reporting_does_not_notify_the_author(client, make_user):
    author_headers, _ = make_user("ada")
    reporter_headers, _ = make_user("grace")
    post = _post(client, author_headers)
    client.post(f"/api/posts/{post['id']}/report", headers=reporter_headers, json={"reason": "spam"})

    notes = client.get("/api/notifications", headers=author_headers).json()
    items = notes["items"] if isinstance(notes, dict) else notes
    assert all(item.get("kind") != "report" for item in items)


def test_admin_queue_lists_and_dismisses(client, make_user, make_admin):
    author_headers, _ = make_user("ada")
    reporter_headers, _ = make_user("grace")
    admin_headers, _ = make_admin("root")
    post = _post(client, author_headers)
    report = client.post(
        f"/api/posts/{post['id']}/report", headers=reporter_headers, json={"reason": "explicit"}
    ).json()

    queue = client.get("/api/admin/reports", headers=admin_headers)
    assert queue.status_code == 200
    assert [item["id"] for item in queue.json()["items"]] == [report["id"]]
    assert queue.json()["items"][0]["author_username"] == "ada"
    assert queue.json()["items"][0]["reporter_username"] == "grace"

    resolved = client.post(
        f"/api/admin/reports/{report['id']}/resolve", headers=admin_headers, json={"action": "dismiss"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "dismissed"
    # A dismissed report leaves the post alone and the open queue empty.
    assert client.get(f"/api/posts/{post['id']}", headers=admin_headers).status_code == 200
    assert client.get("/api/admin/reports", headers=admin_headers).json()["total"] == 0


def test_removing_a_post_closes_every_report_about_it(client, make_user, make_admin, db_session):
    author_headers, _ = make_user("ada")
    first_headers, _ = make_user("grace")
    second_headers, _ = make_user("linus")
    admin_headers, admin = make_admin("root")
    post = _post(client, author_headers)

    first = client.post(
        f"/api/posts/{post['id']}/report", headers=first_headers, json={"reason": "spam"}
    ).json()
    client.post(f"/api/posts/{post['id']}/report", headers=second_headers, json={"reason": "other"})

    removed = client.post(
        f"/api/admin/reports/{first['id']}/resolve", headers=admin_headers, json={"action": "remove"}
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "actioned"
    assert removed.json()["post_deleted"] is True
    assert db_session.get(Post, post["id"]) is None
    # Both complaints are closed, so the next moderator is not asked again.
    assert client.get("/api/admin/reports", headers=admin_headers).json()["total"] == 0
    statuses = {row.status for row in db_session.query(PostReport).all()}
    assert statuses == {"actioned"}
    assert {row.resolved_by_id for row in db_session.query(PostReport).all()} == {admin["id"]}


def test_reports_are_admin_only(client, make_user):
    headers, _ = make_user("grace")
    assert client.get("/api/admin/reports", headers=headers).status_code == 403
