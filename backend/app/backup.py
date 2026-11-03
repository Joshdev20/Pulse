"""Export and import the entire Pulse database as one portable file.

    python -m app.backup export  --out pulse-backup.json.gz
    python -m app.backup verify  --in  pulse-backup.json.gz
    python -m app.backup restore --in  pulse-backup.json.gz --replace

The file is gzipped NDJSON: one JSON object per line, written table by table in
foreign-key-safe order, with a manifest trailer carrying the row counts and a
SHA-256 over every line above it. Rows are streamed rather than buffered, so an
export whose media blobs dominate the database does not need memory proportional
to it. A restore runs inside a single transaction and rolls back as a unit when
the checksum does not match, so a truncated or corrupted file cannot leave a
half-imported database behind.

This exists because a hosted Postgres can suspend when a plan's quota runs out,
and because the media bytes live in the database rather than in object storage.
Both make a complete, self-contained copy of the data worth having on hand.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import sys
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Integer, Table, select, text
from sqlalchemy.engine import Connection

from . import models  # noqa: F401  - registers every table on Base.metadata
from .database import Base, engine

FORMAT = "pulse-backup"
VERSION = 1
BATCH = 200
SUFFIX = ".json.gz"


# --- value encoding -------------------------------------------------------


def _encode(value: Any) -> Any:
    """Turn a column value into something JSON can hold.

    Bytes are base64 rather than latin-1 because a JSON string must survive a
    round trip through another tool; the enclosing gzip stream is what keeps the
    33% expansion from mattering.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    return str(value)


def _decode(value: Any) -> Any:
    """Reverse :func:`_encode`. Plain values pass through untouched."""
    if isinstance(value, dict):
        if "$bytes" in value:
            return base64.b64decode(value["$bytes"])
        if "$datetime" in value:
            return datetime.fromisoformat(value["$datetime"])
        if "$date" in value:
            return date.fromisoformat(value["$date"])
    return value


def _line(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class _Trailer:
    """Where the reader leaves the manifest and the hash of the data lines.

    The manifest is the file's last line, but its hash covers only the lines
    before it, so the reader has to hold the last line back until it knows it
    really is the last one. This is where that line, and the running digest,
    end up once the data lines are exhausted.
    """

    manifest: dict[str, Any] | None = None
    sha256: str = ""
    counts: dict[str, int] = {}

    def reset(self) -> None:
        self.manifest = None
        self.sha256 = ""
        self.counts = {}


def _records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield each data row, filling the module-level :data:`_trailer` as it goes.

    Every yielded row has been folded into the running digest and the row
    counts, so by the time the generator is exhausted the trailer holds
    everything needed to check the file.
    """
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    pending: str | None = None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for raw in handle:
            payload = raw.rstrip("\n")
            if not payload:
                continue
            if pending is not None:
                record = json.loads(pending)
                digest.update(pending.encode("utf-8"))
                digest.update(b"\n")
                counts[record["t"]] = counts.get(record["t"], 0) + 1
                yield record
            pending = payload
    if pending is None:
        raise ValueError(f"{path} is empty")
    trailer = json.loads(pending)
    manifest = trailer.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("last line is not a manifest; the file is truncated")
    _trailer.manifest = manifest
    _trailer.sha256 = digest.hexdigest()
    _trailer.counts = counts


_trailer = _Trailer()


def _check_header(manifest: dict[str, Any]) -> None:
    if manifest.get("format") != FORMAT:
        raise ValueError(f"not a Pulse backup (format={manifest.get('format')!r})")
    if manifest.get("version") != VERSION:
        raise ValueError(
            f"unsupported backup version {manifest.get('version')!r}; this build reads {VERSION}"
        )


def _check_integrity(manifest: dict[str, Any]) -> None:
    _check_header(manifest)
    if _trailer.sha256 != manifest.get("sha256"):
        raise ValueError("checksum mismatch: the file is corrupted or was truncated")
    # An empty table writes no lines, so the reader never sees its name. Compare
    # over every table the schema knows, treating an absent one as zero rows.
    actual = {table.name: _trailer.counts.get(table.name, 0) for table in Base.metadata.sorted_tables}
    if manifest.get("tables") != actual:
        raise ValueError(
            f"row counts do not match the manifest: {actual} != {manifest.get('tables')}"
        )


# --- export ---------------------------------------------------------------


def export(out_path: Path) -> dict[str, Any]:
    """Write every table to ``out_path``. Returns the manifest it wrote."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    counts: dict[str, int] = {}

    with engine.connect() as conn:
        # One transaction for the whole export so every table is read from the
        # same point in time, and a server-side cursor so the media table's
        # blobs are not all resident at once.
        with conn.begin():
            stream = conn.execution_options(stream_results=True, yield_per=BATCH)
            with gzip.open(out_path, "wt", encoding="utf-8") as handle:
                for table in Base.metadata.sorted_tables:
                    count = 0
                    for row in stream.execute(select(table)).mappings():
                        payload = _line(
                            {"t": table.name, "r": {col: _encode(row[col]) for col in row.keys()}}
                        )
                        handle.write(payload + "\n")
                        digest.update(payload.encode("utf-8"))
                        digest.update(b"\n")
                        count += 1
                    counts[table.name] = count

    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dialect": engine.dialect.name,
        "tables": counts,
        "rows": sum(counts.values()),
        "sha256": digest.hexdigest(),
    }
    # Appended rather than prepended: the counts and the digest are only known
    # once every row has been streamed, and the reader hashes lines until it
    # reaches this one.
    with gzip.open(out_path, "at", encoding="utf-8") as handle:
        handle.write(_line({"manifest": manifest}) + "\n")
    return manifest


# --- restore --------------------------------------------------------------


def _reset_sequences(conn: Connection) -> None:
    """Advance each Postgres identity sequence past the ids just inserted.

    A restore writes explicit primary keys, which leaves the sequence untouched.
    Without this the next insert reuses an id that is already taken and fails.
    """
    if conn.dialect.name != "postgresql":
        return
    for table in Base.metadata.sorted_tables:
        pk = list(table.primary_key.columns)
        if len(pk) != 1 or not isinstance(pk[0].type, Integer):
            continue
        sequence = conn.execute(
            text("SELECT pg_get_serial_sequence(:t, :c)"),
            {"t": table.name, "c": pk[0].name},
        ).scalar()
        if not sequence:
            continue
        highest = conn.execute(
            text(f'SELECT COALESCE(MAX("{pk[0].name}"), 0) FROM "{table.name}"')
        ).scalar()
        # `called=false` for an empty table so the first generated id is 1, not 2.
        conn.execute(
            text("SELECT setval(:s, :v, :called)"),
            {"s": sequence, "v": highest or 1, "called": bool(highest)},
        )


class _Inserter:
    """Buffers rows for one table and flushes them in batches."""

    def __init__(self, conn: Connection, replace: bool) -> None:
        self._conn = conn
        self._replace = replace
        self._table: Table | None = None
        self._rows: list[dict[str, Any]] = []

    def add(self, table: Table, row: dict[str, Any]) -> None:
        # A backup is written table by table, so a change of name means the
        # previous table is complete. Buffering across that boundary would
        # insert rows into the wrong table.
        if self._table is not None and self._table.name != table.name:
            self.flush()
        self._table = table
        self._rows.append(row)
        if len(self._rows) >= BATCH:
            self.flush()

    def flush(self) -> None:
        if not self._rows or self._table is None:
            return
        if self._replace:
            # Cleared once, just before the first insert of the restore, so an
            # interrupted run cannot leave the database emptied.
            for table in Base.metadata.sorted_tables:
                self._conn.execute(table.delete())
            self._replace = False
        self._conn.execute(self._table.insert(), self._rows)
        self._rows.clear()


def restore(in_path: Path, replace: bool = False) -> dict[str, Any]:
    """Load ``in_path`` into the configured database.

    Refuses to touch a database that already holds rows unless ``replace`` is
    set, mirroring the way seeding declines to overwrite live data. The file is
    checked in full before anything is written, so a corrupt backup is rejected
    rather than half-applied, and the writes themselves share one transaction.
    """
    # Check first: a corrupt file must not reach the database at all, and the
    # insert pass below would otherwise fail on a constraint before the
    # checksum ever got compared.
    manifest = verify(in_path)

    tables = {table.name: table for table in Base.metadata.sorted_tables}
    _trailer.reset()

    with engine.begin() as conn:
        inserter = _Inserter(conn, replace)
        for record in _records(in_path):
            name = record["t"]
            if name not in tables:
                raise ValueError(f"backup contains unknown table {name!r}")
            inserter.add(
                tables[name],
                {key: _decode(value) for key, value in record["r"].items()},
            )
        inserter.flush()
        _reset_sequences(conn)
    return manifest


# --- verify ---------------------------------------------------------------


def verify(in_path: Path) -> dict[str, Any]:
    """Check a backup parses, matches its checksum and matches its counts."""
    _trailer.reset()
    for _ in _records(in_path):
        pass
    _check_integrity(_trailer.manifest or {})
    return _trailer.manifest or {}


# --- cli ------------------------------------------------------------------


def _default_out() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path(f"pulse-backup-{stamp}{SUFFIX}")


def _describe(manifest: dict[str, Any]) -> str:
    tables = manifest.get("tables") or {}
    populated = ", ".join(f"{name}={count}" for name, count in sorted(tables.items()) if count)
    return (
        f"{manifest.get('rows', 0)} row(s) across {len(tables)} table(s)"
        f"\ncreated {manifest.get('created_at')} from {manifest.get('dialect')}"
        f"\n{populated or '(all tables empty)'}"
    )


def _occupied_tables() -> list[str]:
    with engine.connect() as conn:
        return [
            table.name
            for table in Base.metadata.sorted_tables
            if conn.execute(select(table).limit(1)).first() is not None
        ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export or import the whole Pulse database.")
    sub = parser.add_subparsers(dest="command", required=True)

    exporter = sub.add_parser("export", help="write every table to a backup file")
    exporter.add_argument("--out", type=Path, default=None, help="destination file")

    importer = sub.add_parser("restore", help="load a backup file into this database")
    importer.add_argument("--in", dest="source", type=Path, required=True)
    importer.add_argument(
        "--replace",
        action="store_true",
        help="delete existing rows first (required when the database is not empty)",
    )

    checker = sub.add_parser("verify", help="check a backup file without loading it")
    checker.add_argument("--in", dest="source", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "export":
        out = args.out or _default_out()
        manifest = export(out)
        print(f"Wrote {out} ({out.stat().st_size / 1024:.1f} KiB)")
        print(_describe(manifest))
        return 0

    if not args.source.exists():
        print(f"No such file: {args.source}", file=sys.stderr)
        return 1

    if args.command == "verify":
        try:
            manifest = verify(args.source)
        except (ValueError, OSError, EOFError) as exc:
            print(f"Invalid backup: {exc}", file=sys.stderr)
            return 1
        print(f"OK {args.source}")
        print(_describe(manifest))
        return 0

    if not args.replace and (occupied := _occupied_tables()):
        print(
            "Refusing to restore into a database that already has data "
            f"({', '.join(occupied)}).\nRe-run with --replace to overwrite it.",
            file=sys.stderr,
        )
        return 1

    try:
        manifest = restore(args.source, replace=args.replace)
    except (ValueError, OSError, EOFError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"Restored {args.source}")
    print(_describe(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
