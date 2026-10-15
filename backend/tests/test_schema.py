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
    assert not {"is_admin", "is_banned", "banned_reason"} & before

    applied = ensure_schema(legacy_engine)

    assert set(applied) == {"users.is_admin", "users.is_banned", "users.banned_reason"}
    after = {column["name"] for column in inspect(legacy_engine).get_columns("users")}
    assert {"is_admin", "is_banned", "banned_reason"} <= after


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
