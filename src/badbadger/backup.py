"""Create verified SQLite snapshots; restoration always targets a new file."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from badbadger.scenarios.catalog import CHAPTERS


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
                if scenario is None or scenario[0] not in {value["scenario_id"] for value in CHAPTERS.values()}:
                    raise ValueError("Expected a supported BadBadger web save")
                revision = writer.execute("SELECT revision FROM player_session WHERE id=1").fetchone()
                count, maximum = writer.execute("SELECT count(*), coalesce(max(revision),0) FROM player_turns").fetchone()
                if revision is None or revision[0] != count or revision[0] != maximum:
                    raise ValueError("Web save revision and transcript disagree")
        # Flush before exposing the backup and never replace an existing file,
        # even if another process creates it while this snapshot is being made.
        # Windows requires a writable descriptor for FlushFileBuffers (fsync).
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        if os.name == "nt":
            # Windows rename fails if the destination exists, including races,
            # and avoids requiring hard-link privileges in restricted accounts.
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
        # POSIX supports syncing directory entries; Windows exposes no portable
        # directory fsync through os.open. The file itself is flushed above.
        if os.name == "posix":
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup_testers(source: str | Path, destination: str | Path) -> Path:
    """Snapshot every tester database, including saves of retired accounts.

    A batch is complete only when manifest.json exists. Failed batches remain
    available for diagnosis but are never reported as complete. Each save is
    consistent independently; this is not a simultaneous cross-player snapshot.
    """
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if not source.is_dir() or destination == source or source in destination.parents:
        raise ValueError("Choose a backup directory outside the tester save directory")
    saves = sorted(source.glob("*.db"))
    if not saves or any(path.is_symlink() or not path.is_file()
                        or not re.fullmatch(r"[0-9a-f]{64}\.db", path.name) for path in saves):
        raise ValueError("Expected regular tester database files with hashed account filenames")
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    entries = []
    for save in saves:
        copy = snapshot(save, destination / save.name)
        with closing(sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True)) as connection:
            revision = connection.execute("SELECT revision FROM player_session WHERE id=1").fetchone()[0]
        entries.append({"file": save.name, "sha256": _digest(copy), "revision": revision})
    # Detect account/save creation during the run rather than claim full coverage.
    if [path.name for path in sorted(source.glob("*.db"))] != [path.name for path in saves]:
        raise ValueError("Tester save inventory changed; retry into a new backup directory")
    manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(), "saves": entries}
    temporary = destination / ".manifest.pending"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, destination / "manifest.json")
    if os.name == "posix":
        directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return destination


def restore_testers(source: str | Path, destination: str | Path) -> Path:
    """Restore a complete batch to a new directory; never modify live saves."""
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination == source or source in destination.parents:
        raise ValueError("Restore must target a separate new directory")
    manifest_path = source / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Manifest must be a regular file")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise ValueError("Unsupported backup manifest")
    entries = manifest.get("saves")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Empty backup manifest")
    names = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("file"), str)
                or not re.fullmatch(r"[0-9a-f]{64}\.db", entry["file"])
                or type(entry.get("revision")) is not int or entry["revision"] < 0
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or entry["file"] in names):
            raise ValueError("Invalid or duplicate backup filename")
        names.add(entry["file"])
        path = source / entry["file"]
        if path.is_symlink() or not path.is_file() or _digest(path) != entry.get("sha256"):
            raise ValueError("Backup checksum mismatch or missing save")
    if names != {path.name for path in source.glob("*.db")}:
        raise ValueError("Backup inventory disagrees with manifest")
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    for entry in entries:
        restored = snapshot(source / entry["file"], destination / entry["file"])
        if _digest(source / entry["file"]) != entry["sha256"]:
            raise ValueError("Backup changed during restoration")
        with closing(sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True)) as connection:
            revision = connection.execute("SELECT revision FROM player_session WHERE id=1").fetchone()[0]
        if revision != entry.get("revision"):
            raise ValueError("Restored revision disagrees with manifest")
    return destination


def main():
    parser = argparse.ArgumentParser(description="Verified BadBadger SQLite backup/restore (never overwrites)")
    parser.add_argument("operation", choices=("backup", "restore", "backup-testers", "restore-testers"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        operation = {"backup-testers": backup_testers, "restore-testers": restore_testers}.get(args.operation, snapshot)
        result = operation(args.source, args.destination)
    except (OSError, sqlite3.Error, ValueError):
        parser.exit(1, "Save copy failed. Check paths, integrity and permissions; existing destinations were not replaced. A failed batch may leave a partial new directory; retry with a new destination.\n")
    print(f"Verified {args.operation} written to {result}")


if __name__ == "__main__":
    main()
