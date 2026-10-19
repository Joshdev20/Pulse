"""Forward-only schema migrations for columns added after a table exists.

``Base.metadata.create_all`` only creates missing *tables*; it never adds a
column to a table that already exists. A deploy against an existing database —
the hosted Postgres instance, or a developer's local ``pulse.db`` — therefore
needs new columns added explicitly, or every query touching them fails.

Alembic would be the long-term answer. For the handful of additive columns this
project needs, an idempotent ``ALTER TABLE`` keeps the deploy to a single
process with no migration step to run by hand, and cannot lose data: it only
ever adds columns.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# (table, column, column definition). Additive only — never destructive.
_ADDITIONS: list[tuple[str, str, str]] = [
    ("users", "is_admin", "BOOLEAN NOT NULL DEFAULT false"),
    ("users", "is_banned", "BOOLEAN NOT NULL DEFAULT false"),
    ("users", "banned_reason", "VARCHAR(280) NOT NULL DEFAULT ''"),
    ("users", "is_verified", "BOOLEAN NOT NULL DEFAULT false"),
    # Nullable, so no default is needed; existing users keep the generated
    # initials avatar until they upload a picture.
    ("users", "verified_at", "TIMESTAMP NULL"),
    ("users", "avatar_media_id", "INTEGER NULL"),
    ("users", "cover_media_id", "INTEGER NULL"),
    # Last-seen tracking for the message thread header. Nullable: an account
    # that has not been active since the upgrade has no answer yet.
    ("users", "last_seen_at", "TIMESTAMP NULL"),
    # Nullable: only reposts carry it. No FK constraint is declared here because
    # ALTER TABLE cannot add one to an existing table on every backend, and the
    # repost endpoints delete their rows explicitly rather than leaning on a
    # cascade that a legacy database would not have.
    ("posts", "repost_of_id", "INTEGER NULL"),
    # Replies. Nullable, so a database that predates threading keeps every
    # existing row as a top-level comment.
    ("comments", "parent_id", "INTEGER NULL"),
    # The file bytes, moved into the database so media survives a host whose
    # filesystem is ephemeral. Nullable: rows written before this column keep
    # working through the on-disk fallback until they are backfilled.
    ("media", "blob", "BYTEA NULL"),
    # The video preview frame and its type, captured in the browser at upload.
    # Nullable: a video uploaded before this column existed keeps working
    # through the in-browser capture until it is re-uploaded.
    ("media", "poster_content_type", "VARCHAR(64) NULL"),
    ("media", "poster_blob", "BYTEA NULL"),
    ("media", "duration_ms", "INTEGER NULL"),
    # Swipe-to-reply. Nullable, so every message written before this column
    # stays a plain message. No FK is added by ALTER TABLE; the serializer
    # tolerates a quote whose target is gone.
    ("messages", "reply_to_id", "INTEGER NULL"),
    # Web Push endpoint for a browser, so alerts reach a closed tab.
    ("push_subscriptions", "user_agent", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("push_subscriptions", "last_used_at", "TIMESTAMP NULL"),
]


def ensure_schema(engine: Engine) -> list[str]:
    """Add any expected column the database is missing.

    Returns the ``table.column`` names that were added, so the caller can log
    them. Safe to run on every startup: existing columns are skipped.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    with engine.begin() as connection:
        for table, column, definition in _ADDITIONS:
            # A table that does not exist yet is created complete by create_all.
            if table not in existing_tables:
                continue
            if column in {col["name"] for col in inspector.get_columns(table)}:
                continue
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
            applied.append(f"{table}.{column}")

    return applied
