"""Recording a view must stay a fixed cost, not one write per post.

A feed page records a view for every post it shows. When each of those was its
own `INSERT` committed in turn, the read path paid a round trip per post — and
against the hosted database that latency landed directly on time to first byte.
These tests pin the statement count so the batching cannot quietly regress.
"""

from sqlalchemy import event

from app.database import get_db
from app.deps import record_post_views


def _statements(client, fn):
    """Run ``fn`` and return every statement it issued."""
    override = client.app.dependency_overrides[get_db]
    session = next(override())
    engine = session.get_bind()
    session.close()

    issued: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        issued.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return issued


def _viewer(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]


def test_first_view_of_many_posts_is_a_fixed_number_of_statements(client, make_user):
    """Twelve unseen posts must not cost twelve inserts."""
    ada, _ = make_user("ada")
    author, _ = make_user("grace")
    ids = [
        client.post("/api/posts", json={"body": f"post {n}"}, headers=author).json()["id"]
        for n in range(12)
    ]

    viewer_id = _viewer(client, ada)
    from app.models import User

    override = client.app.dependency_overrides[get_db]
    db = next(override())
    viewer = db.get(User, viewer_id)
    db.close()

    def run():
        session = next(override())
        try:
            record_post_views(session, ids, session.get(User, viewer.id))
        finally:
            session.close()

    issued = _statements(client, run)
    inserts = [s for s in issued if s.lstrip().upper().startswith("INSERT")]

    assert len(inserts) == 1, f"expected one batched insert, got {len(inserts)}: {inserts}"
    # Author lookup + already-seen lookup + the one insert. Nothing per post.
    assert len(issued) <= 4, f"view recording grew with the post count: {issued}"


def test_second_view_of_the_same_posts_writes_nothing(client, make_user):
    """Re-reading a page already seen must be read-only."""
    ada, _ = make_user("ada")
    author, _ = make_user("grace")
    ids = [
        client.post("/api/posts", json={"body": f"post {n}"}, headers=author).json()["id"]
        for n in range(6)
    ]

    from app.models import User

    override = client.app.dependency_overrides[get_db]
    viewer_id = _viewer(client, ada)

    def run():
        session = next(override())
        try:
            record_post_views(session, ids, session.get(User, viewer_id))
        finally:
            session.close()

    run()  # first read records the views

    issued = _statements(client, run)
    writes = [
        s
        for s in issued
        if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]

    assert writes == [], f"a repeat read should not write: {writes}"
