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

from .models import utcnow

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

    Reads every column of every table in one statement. ``inspect(...).get_columns``
    reflects one table per round trip, and this runs on each boot against a
    database that is often a continent away — the old per-table loop cost ~20
    round trips before the process could answer its first request.
    """
    existing_tables = set(inspect(engine).get_table_names())
    columns = _column_map(engine, {table for table, _, _ in _ADDITIONS} & existing_tables)
    applied: list[str] = []

    with engine.begin() as connection:
        for table, column, definition in _ADDITIONS:
            # A table that does not exist yet is created complete by create_all.
            if table not in existing_tables:
                continue
            if column in columns.get(table, set()):
                continue
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
            applied.append(f"{table}.{column}")

    applied.extend(_backfill_push_links(engine, existing_tables))
    return applied


def _column_map(engine: Engine, tables: set[str]) -> dict[str, set[str]]:
    """Every table's column names, read with a single query where possible.

    Postgres answers from ``information_schema`` in one statement. SQLite has no
    equivalent, but it is a local file in this project, so a per-table pragma
    there costs nothing worth optimising.
    """
    if not tables:
        return {}
    if engine.dialect.name != "sqlite":
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = ANY(:names)"
                    ),
                    {"names": list(tables)},
                ).all()
            found: dict[str, set[str]] = {table: set() for table in tables}
            for table_name, column_name in rows:
                found.setdefault(table_name, set()).add(column_name)
            return found
        except Exception:  # noqa: BLE001 - fall back rather than fail the boot
            # A schema read must never be the reason the app cannot start, so any
            # surprise here degrades to the slow-but-known reflection path.
            pass

    inspector = inspect(engine)
    return {table: {col["name"] for col in inspector.get_columns(table)} for table in tables}


def _backfill_push_links(engine: Engine, tables: set[str] | None = None) -> list[str]:
    """Give every existing subscription a link to the account it was stored for.

    The link table is new, and until a browser re-subscribes its endpoint has no
    rows in it — which would mean delivery silently stopped for every device that
    was already registered. Each existing ``push_subscriptions.user_id`` becomes
    the first link, which is exactly the account that row used to serve.

    Idempotent: a link that is already there is skipped, so this is safe on every
    startup.
    """
    inspector = inspect(engine)
    tables = tables if tables is not None else set(inspector.get_table_names())
    if "push_subscriptions" not in tables or "push_subscription_users" not in tables:
        return []

    with engine.begin() as connection:
        result = connection.execute(
            text(
                "INSERT INTO push_subscription_users (subscription_id, user_id, created_at) "
                "SELECT s.id, s.user_id, :now FROM push_subscriptions s "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM push_subscription_users l "
                "  WHERE l.subscription_id = s.id AND l.user_id = s.user_id"
                ")"
            ),
            {"now": utcnow()},
        )
        count = result.rowcount or 0

    return [f"push_subscription_users:{count} link(s) backfilled"] if count else []
