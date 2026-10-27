"""Create verified SQLite snapshots; restoration always targets a new file."""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile


def snapshot(source: str | Path, destination: str | Path) -> Path:
    """Back up a live web save or restore a snapshot without overwriting files.

    SQLite's backup API includes committed WAL changes. The destination is
    published only after integrity checks, with private file permissions.
    """
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Destination already exists; choose a new path")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".badbadger-", suffix=".db", dir=destination.parent, delete=False) as file:
            temporary = Path(file.name)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(temporary)) as writer:
                reader.backup(writer)
                if writer.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite integrity check failed")
                if writer.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("SQLite foreign key check failed")
                scenario = writer.execute("SELECT scenario_id FROM simulation WHERE id=1").fetchone()
                if scenario != ("relay-crisis-0.1",):
                    raise ValueError("Expected a Relay Crisis web save")
                revision = writer.execute("SELECT revision FROM player_session WHERE id=1").fetchone()
                count, maximum = writer.execute("SELECT count(*), coalesce(max(revision),0) FROM player_turns").fetchone()
                if revision is None or revision[0] != count or revision[0] != maximum:
                    raise ValueError("Web save revision and transcript disagree")
        # Flush before exposing the backup and never replace an existing file,
        # even if another process creates it while this snapshot is being made.
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        os.link(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Verified Relay Crisis SQLite backup/restore (never overwrites)")
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = snapshot(args.source, args.destination)
    except (OSError, sqlite3.Error, ValueError):
        parser.exit(1, "Save copy failed. Check the paths, save integrity and permissions; no existing destination was replaced.\n")
    print(f"Verified {args.operation} written to {result}")


if __name__ == "__main__":
    main()
