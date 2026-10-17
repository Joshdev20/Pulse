"""Schema migration and moderator backfill against a pre-existing database.

``Base.metadata.create_all`` creates missing tables but never adds a column to a
table that already exists, so a deployment onto an older database needs
``ensure_schema``. Those rows also keep the new column's default, which is how a
deployment ends up with users but no moderator: ``seed`` is a no-op when the
database is not empty, so nothing ever sets ``is_admin``.

These tests build a legacy ``users`` table by hand, then run the migration and
the backfill the way startup does.
"""

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.models import User
from app.schema import ensure_schema
from app.seed import ensure_admin

# The `users` table as it existed before the admin columns were added.
_LEGACY_USERS_DDL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username VARCHAR(32) NOT NULL,
    email VARCHAR(255) NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    bio VARCHAR(280) NOT NULL DEFAULT '',
    location VARCHAR(80) NOT NULL DEFAULT '',
    website VARCHAR(200) NOT NULL DEFAULT '',
    avatar_color VARCHAR(16) NOT NULL DEFAULT '#b8f24a',
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture
def legacy_engine():
    """An on-disk database holding the old schema, so ALTER TABLE really runs."""
    import tempfile

    path = tempfile.mktemp(suffix=".db")
    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as connection:
        connection.execute(text(_LEGACY_USERS_DDL))
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, display_name, password_hash) "
                "VALUES (1, 'ada', 'ada@example.com', 'Ada', 'x'), "
                "(2, 'grace', 'grace@example.com', 'Grace', 'x')"
            )
        )
    yield engine
    engine.dispose()
    import os

    os.unlink(path)


def _session_for(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def test_ensure_schema_adds_the_admin_columns(legacy_engine):
    before = {column["name"] for column in inspect(legacy_engine).get_columns("users")}
    expected = {
        "is_admin",
        "is_banned",
        "banned_reason",
        "is_verified",
        "verified_at",
        "avatar_media_id",
        "cover_media_id",
        "last_seen_at",
    }
    assert not expected & before

    applied = ensure_schema(legacy_engine)

    assert set(applied) == {f"users.{column}" for column in expected}
    after = {column["name"] for column in inspect(legacy_engine).get_columns("users")}
    assert expected <= after


def test_ensure_schema_adds_repost_of_to_an_existing_posts_table(legacy_engine):
    """An older database gains repost_of_id, so reposts work after an upgrade.

    The legacy fixture has no `posts` table at all, which is why the test above
    sees only the user columns: `ensure_schema` skips a table that create_all
    will build complete.
    """
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE posts ("
                "id INTEGER PRIMARY KEY, author_id INTEGER NOT NULL, "
                "body TEXT NOT NULL, created_at DATETIME NOT NULL, edited_at DATETIME)"
            )
        )
    assert "repost_of_id" not in {
        column["name"] for column in inspect(legacy_engine).get_columns("posts")
    }

    applied = ensure_schema(legacy_engine)
    assert "posts.repost_of_id" in applied
    assert "repost_of_id" in {
        column["name"] for column in inspect(legacy_engine).get_columns("posts")
    }


def test_existing_posts_are_not_reposts(legacy_engine):
    """The migrated column is null, so nothing already posted becomes a share."""
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE posts ("
                "id INTEGER PRIMARY KEY, author_id INTEGER NOT NULL, "
                "body TEXT NOT NULL, created_at DATETIME NOT NULL, edited_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO posts (id, author_id, body, created_at) "
                "VALUES (1, 1, 'an original', CURRENT_TIMESTAMP)"
            )
        )
    ensure_schema(legacy_engine)
    with _session_for(legacy_engine)() as session:
        row = session.execute(text("SELECT repost_of_id FROM posts WHERE id = 1")).one()
    assert row[0] is None


def test_ensure_schema_adds_blob_to_an_existing_media_table(legacy_engine):
    """An older database gains `media.blob`, so media can be stored durably.

    The column is nullable: an existing row loads with a null blob and the
    download route falls back to the file on disk (or backfills it at startup),
    so the migration never strands a row it cannot fill.
    """
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE media ("
                "id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, "
                "stored_name VARCHAR(255) NOT NULL, content_type VARCHAR(64) NOT NULL, "
                "kind VARCHAR(16) NOT NULL, size_bytes INTEGER NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO media (id, owner_id, stored_name, content_type, kind, size_bytes) "
                "VALUES (1, 1, 'x.png', 'image/png', 'image', 74)"
            )
        )
    assert "blob" not in {
        column["name"] for column in inspect(legacy_engine).get_columns("media")
    }

    applied = ensure_schema(legacy_engine)
    assert "media.blob" in applied

    with _session_for(legacy_engine)() as session:
        blob = session.execute(text("SELECT blob FROM media WHERE id = 1")).scalar()
    assert blob is None


def test_ensure_schema_adds_reply_to_id_to_an_existing_messages_table(legacy_engine):
    """An older database gains `messages.reply_to_id`, so replies work.

    Nullable: every message written before the upgrade stays a plain message
    rather than being read as a reply to message 0.
    """
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE messages ("
                "id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, "
                "sender_id INTEGER NOT NULL, body TEXT NOT NULL DEFAULT '', "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO messages (id, conversation_id, sender_id, body) "
                "VALUES (1, 1, 1, 'an original')"
            )
        )
    assert "reply_to_id" not in {
        column["name"] for column in inspect(legacy_engine).get_columns("messages")
    }

    applied = ensure_schema(legacy_engine)
    assert "messages.reply_to_id" in applied

    with _session_for(legacy_engine)() as session:
        row = session.execute(text("SELECT reply_to_id FROM messages WHERE id = 1")).one()
    assert row[0] is None


def test_ensure_schema_is_idempotent(legacy_engine):
    ensure_schema(legacy_engine)
    assert ensure_schema(legacy_engine) == []


def test_existing_rows_are_not_administrators(legacy_engine):
    """The migrated column defaults to false, which is the state to repair."""
    ensure_schema(legacy_engine)
    with _session_for(legacy_engine)() as session:
        admins = session.scalars(select(User).where(User.is_admin.is_(True))).all()
    assert admins == []


def test_ensure_admin_promotes_the_oldest_account(legacy_engine, monkeypatch):
    ensure_schema(legacy_engine)
    monkeypatch.setattr("app.seed.SessionLocal", _session_for(legacy_engine))

    assert ensure_admin() == "ada"

    with _session_for(legacy_engine)() as session:
        admins = [user.username for user in session.scalars(select(User).where(User.is_admin.is_(True)))]
    assert admins == ["ada"]


def test_ensure_admin_leaves_an_existing_admin_alone(legacy_engine, monkeypatch):
    ensure_schema(legacy_engine)
    Session = _session_for(legacy_engine)
    with Session() as session:
        session.get(User, 2).is_admin = True
        session.commit()
    monkeypatch.setattr("app.seed.SessionLocal", Session)

    assert ensure_admin() is None

    with Session() as session:
        admins = [user.username for user in session.scalars(select(User).where(User.is_admin.is_(True)))]
    assert admins == ["grace"]


def test_ensure_admin_is_idempotent(legacy_engine, monkeypatch):
    ensure_schema(legacy_engine)
    monkeypatch.setattr("app.seed.SessionLocal", _session_for(legacy_engine))

    assert ensure_admin() == "ada"
    assert ensure_admin() is None


def test_ensure_admin_on_an_empty_database_does_nothing(legacy_engine, monkeypatch):
    """With no accounts there is nobody to promote, and it must not raise."""
    ensure_schema(legacy_engine)
    Session = _session_for(legacy_engine)
    with Session() as session:
        session.query(User).delete()
        session.commit()
    monkeypatch.setattr("app.seed.SessionLocal", Session)

    assert ensure_admin() is None
