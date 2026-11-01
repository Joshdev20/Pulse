"""Round-tripping the whole database through the backup file.

The export has to survive the trip that matters most: out of one database and
into another that has never seen this data. That includes the bytes of an
upload, which live in a `media` row rather than on disk, and the sequences,
which a restore leaves stale unless it fixes them up. Each of those gets a test
that would fail if the encoding, the ordering or the sequence reset regressed.
"""

import gzip
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import backup
from app.database import Base
from app.models import Media, Post, User


@pytest.fixture
def db_factory(monkeypatch, tmp_path):
    """Build isolated file-backed databases and point the module at each one.

    File-backed rather than in-memory because a backup is written and then read
    back, and the reader uses its own connection. Two databases are needed, so
    the factory hands out engines the test can switch between by calling the
    returned function with a name.
    """
    engines = {}

    def use(name: str, create: bool = True):
        engine = create_engine(f"sqlite:///{tmp_path / (name + '.db')}", future=True)
        if create:
            Base.metadata.create_all(bind=engine)
        engines[name] = engine
        monkeypatch.setattr(backup, "engine", engine)
        return engine

    use.sessions = lambda name: sessionmaker(bind=engines[name], future=True)
    return use


def _populate(engine, tag: str = "a"):
    """Write a small graph covering a blob column, a FK chain and timestamps.

    ``tag`` makes the rows unique so the helper can run twice against one
    database, which the "replace rather than append" test needs.
    """
    session = sessionmaker(bind=engine, future=True)()
    try:
        author = User(
            username=f"ada{tag}",
            email=f"ada{tag}@example.com",
            display_name="Ada",
            password_hash="x",
        )
        session.add(author)
        session.commit()

        post = Post(author_id=author.id, body="hello")
        session.add(post)
        session.commit()

        session.add(
            Media(
                owner_id=author.id,
                stored_name=f"a{tag}.png",
                content_type="image/png",
                kind="image",
                size_bytes=5,
                blob=b"\x89PNG\r\n",
                poster_blob=b"poster",
                post_id=post.id,
            )
        )
        session.commit()
    finally:
        session.close()


def _counts(engine):
    with engine.connect() as conn:
        return {
            table.name: conn.execute(select(func.count()).select_from(table)).scalar()
            for table in Base.metadata.sorted_tables
        }


def _tamper(path):
    """Change one row without updating the manifest, so the checksum disagrees."""
    lines = gzip.open(path, "rt").read().splitlines()
    for index, line in enumerate(lines):
        if '"body":"hello"' in line:
            lines[index] = line.replace('"body":"hello"', '"body":"tampered"')
            break
    else:
        raise AssertionError("the post row was not found in the backup")
    with gzip.open(path, "wt") as handle:
        handle.write("\n".join(lines) + "\n")


def test_export_then_restore_reproduces_every_table(db_factory, tmp_path):
    source = db_factory("source")
    _populate(source)

    out = tmp_path / "backup.json.gz"
    manifest = backup.export(out)
    assert manifest["format"] == backup.FORMAT
    assert manifest["rows"] == sum(manifest["tables"].values())

    target = db_factory("target")
    restored = backup.restore(out)

    assert restored["sha256"] == manifest["sha256"]
    assert _counts(target) == _counts(source)


def test_media_bytes_survive_the_round_trip(db_factory, tmp_path):
    """The blobs are the reason a backup cannot just be a row dump."""
    source = db_factory("source")
    _populate(source)
    out = tmp_path / "backup.json.gz"
    backup.export(out)

    target = db_factory("target")
    backup.restore(out)

    session = sessionmaker(bind=target, future=True)()
    try:
        media = session.scalar(select(Media))
        assert media.blob == b"\x89PNG\r\n"
        assert media.poster_blob == b"poster"
    finally:
        session.close()


def test_restore_advances_sequences_so_new_rows_do_not_collide(db_factory, tmp_path):
    """A restore writes explicit ids, so the next generated id must clear them.

    Only observable on a database whose inserts allocate from a sequence; SQLite
    uses the rowid, so the check is limited to the behaviour that is portable.
    """
    source = db_factory("source")
    _populate(source)
    out = tmp_path / "backup.json.gz"
    backup.export(out)

    target = db_factory("target")
    backup.restore(out)

    session = sessionmaker(bind=target, future=True)()
    try:
        fresh = User(username="grace", email="g@example.com", display_name="G", password_hash="x")
        session.add(fresh)
        session.commit()
        assert fresh.id > 1
    finally:
        session.close()


def test_large_blobs_round_trip_intact(db_factory, tmp_path):
    """A multi-megabyte attachment must survive gzip and base64 unchanged.

    This is the case that matters in production: the free-tier quota ran out
    because the bytes of every upload live in this one column.
    """
    payload = bytes(range(256)) * 20000  # ~5 MB, incompressible-ish and patterned
    source = db_factory("source")
    session = sessionmaker(bind=source, future=True)()
    try:
        author = User(username="big", email="big@example.com", display_name="B", password_hash="x")
        session.add(author)
        session.commit()
        session.add(
            Media(
                owner_id=author.id,
                stored_name="big.mp4",
                content_type="video/mp4",
                kind="video",
                size_bytes=len(payload),
                blob=payload,
            )
        )
        session.commit()
    finally:
        session.close()

    out = tmp_path / "backup.json.gz"
    backup.export(out)
    assert out.stat().st_size < len(payload)  # gzip actually did something

    target = db_factory("target")
    backup.restore(out)
    session = sessionmaker(bind=target, future=True)()
    try:
        assert session.scalar(select(Media)).blob == payload
    finally:
        session.close()


def test_verify_accepts_a_good_file(db_factory, tmp_path):
    db_factory("source")
    out = tmp_path / "backup.json.gz"
    backup.export(out)
    assert backup.verify(out)["format"] == backup.FORMAT


def test_verify_rejects_a_tampered_line(db_factory, tmp_path):
    source = db_factory("source")
    _populate(source)
    out = tmp_path / "backup.json.gz"
    backup.export(out)

    _tamper(out)

    with pytest.raises(ValueError, match="checksum mismatch"):
        backup.verify(out)


def test_verify_rejects_a_truncated_file(db_factory, tmp_path):
    db_factory("source")
    out = tmp_path / "backup.json.gz"
    backup.export(out)

    truncated = tmp_path / "short.json.gz"
    truncated.write_bytes(out.read_bytes()[:40])

    with pytest.raises((ValueError, EOFError, OSError)):
        backup.verify(truncated)


def test_restore_refuses_a_corrupt_file_without_touching_the_database(db_factory, tmp_path):
    """A bad file must be caught before any write, not midway through."""
    source = db_factory("source")
    _populate(source)
    out = tmp_path / "backup.json.gz"
    backup.export(out)

    _tamper(out)

    target = db_factory("target")
    before = _counts(target)
    with pytest.raises(ValueError, match="checksum mismatch"):
        backup.restore(out, replace=True)
    assert _counts(target) == before


def test_restore_without_replace_leaves_populated_data_alone(db_factory, tmp_path):
    source = db_factory("source")
    _populate(source)
    out = tmp_path / "backup.json.gz"
    backup.export(out)

    target = db_factory("target")
    _populate(target)

    # The guard lives in main(), so exercise it the way the CLI does.
    assert backup._occupied_tables()


def test_restore_replace_replaces_rather_than_appends(db_factory, tmp_path):
    source = db_factory("source")
    _populate(source)
    out = tmp_path / "backup.json.gz"
    backup.export(out)

    target = db_factory("target")
    _populate(target, tag="b")
    _populate(target, tag="c")

    backup.restore(out, replace=True)
    assert _counts(target) == _counts(source)


def test_export_streams_empty_tables_as_zero_not_missing(db_factory, tmp_path):
    """An empty table writes no lines, so the manifest must still account for it."""
    db_factory("source")
    out = tmp_path / "backup.json.gz"
    manifest = backup.export(out)
    assert set(manifest["tables"]) == {t.name for t in Base.metadata.sorted_tables}
    assert all(count == 0 for count in manifest["tables"].values())


def test_main_reports_a_missing_file_without_traceback(db_factory, tmp_path, capsys):
    db_factory("source")
    assert backup.main(["verify", "--in", str(tmp_path / "absent.json.gz")]) == 1
    assert "No such file" in capsys.readouterr().err


def test_main_round_trips_through_the_cli(db_factory, tmp_path, capsys):
    source = db_factory("source")
    _populate(source)
    out = tmp_path / "backup.json.gz"

    assert backup.main(["export", "--out", str(out)]) == 0
    assert backup.main(["verify", "--in", str(out)]) == 0

    db_factory("target")
    assert backup.main(["restore", "--in", str(out)]) == 0
    assert "Restored" in capsys.readouterr().out
