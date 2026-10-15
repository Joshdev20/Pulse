"""Database engine, session factory and declarative base."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def _normalize_url(url: str) -> str:
    """Make a host-provided URL acceptable to SQLAlchemy 2.x.

    Render, Railway and Heroku hand out ``postgres://`` and bare
    ``postgresql://`` URLs. SQLAlchemy 2 dropped the first, and the second
    selects psycopg2 rather than the psycopg 3 driver this project installs.
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


database_url = _normalize_url(settings.database_url)
is_sqlite = database_url.startswith("sqlite")

connect_args = {"check_same_thread": False} if is_sqlite else {}

engine = create_engine(
    database_url,
    connect_args=connect_args,
    future=True,
    # Recycle connections older than a few minutes: a hosted Postgres (or a
    # pooler in front of it) closes idle connections, and a stale one surfaces
    # as a confusing "server closed the connection" on the next request.
    pool_pre_ping=not is_sqlite,
    pool_recycle=280 if not is_sqlite else -1,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """SQLite ignores foreign keys unless they are enabled per connection."""
    if not is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
